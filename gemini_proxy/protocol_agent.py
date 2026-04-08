from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess

from fastapi import HTTPException, UploadFile
import httpx

from .config import Settings
from .markdown_docx import render_markdown_to_docx
from .progress import TimelineTracker
from .schemas import (
    AgentSummary,
    GenerateRequest,
    InputFile,
    ProcessingTimeline,
    ProtocolAgentConfig,
    ProtocolReportState,
    ProtocolResetResponse,
    ProtocolRunResponse,
    UpdateProtocolAgentConfigRequest,
    WebGenerateRequest,
)
from .service import GeminiProxyService
from .web_runner import GeminiWebRunner

DEFAULT_AGENT_ID = "protocol"

LEGACY_ANALYSIS_PROMPT = """Ты помощник по подготовке официальных протоколов встреч.

Проанализируй загруженную запись встречи и подготовь рабочий разбор в Markdown.
Опирайся только на содержание записи, ничего не выдумывай и явно отмечай любые ограничения качества звука, неразборчивые места и неоднозначности.

Верни результат со структурой:
# Рабочий разбор записи
## Краткое содержание
## Участники и роли
## Ход обсуждения по хронологии
## Ключевые тезисы
## Зафиксированные решения
## Поручения и следующие шаги
## Открытые вопросы
## Риски и ограничения записи

Где возможно, добавляй метки времени в формате MM:SS."""

DEFAULT_ANALYSIS_PROMPT = """Ты расшифровываешь один аудиофрагмент встречи на русском языке.

Сделай максимально точную транскрибацию только этого фрагмента. Не додумывай слова и смыслы.
Если часть речи неразборчива, помечай это как [неразборчиво]. Если спикер не идентифицируется, используй нейтральные подписи вроде «Спикер 1», «Спикер 2».

Верни Markdown со структурой:
# Транскрибация фрагмента
## Контекст фрагмента
## Транскрипт
## Неразборчивые и сомнительные места

В разделе «Транскрипт» сохраняй порядок реплик и, где это уместно, добавляй временные метки внутри фрагмента."""

LEGACY_PROTOCOL_PROMPT = """Ты готовишь официальный протокол встречи на русском языке.

Используй загруженную запись и рабочий разбор из текущего чата. Не добавляй фактов, которых нет в записи. Если часть данных не удалось уверенно восстановить, прямо отметь это в тексте.

Верни результат в Markdown со структурой:
# Протокол встречи
## Общие сведения
## Участники
## Ключевые вопросы повестки
## Ход обсуждения
## Принятые решения
## Поручения
## Открытые вопросы
## Риски и оговорки

Пиши официально, ясно и без канцелярской воды."""

DEFAULT_PROTOCOL_PROMPT = """Ты готовишь официальный протокол встречи на русском языке.

Используй только объединенную транскрибацию встречи, собранную из нескольких аудиофрагментов. На границах фрагментов возможны повторы или обрывы реплик из-за нарезки: учитывай это и не дублируй выводы.
Не добавляй фактов, которых нет в транскрибации. Если часть данных не удалось уверенно восстановить, прямо отметь это в тексте.

Верни результат в Markdown со структурой:
# Протокол встречи
## Общие сведения
## Участники
## Ключевые вопросы повестки
## Ход обсуждения
## Принятые решения
## Поручения
## Открытые вопросы
## Риски и оговорки

Пиши официально, ясно и без канцелярской воды."""

AUDIO_EXTENSIONS = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
}

VIDEO_EXTENSIONS = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
}

TARGET_CHUNK_SECONDS = 10 * 60
SINGLE_CHUNK_MAX_SECONDS = 10 * 60
HARD_CHUNK_MAX_SECONDS = 10 * 60
NORMALIZED_AUDIO_SAMPLE_RATE = 16000
NORMALIZED_AUDIO_CHANNELS = 1
NORMALIZED_AUDIO_BITRATE = "64k"
GENERATION_RETRY_ATTEMPTS = 3
GENERATION_RETRY_DELAY_SECONDS = 2
TRANSCRIPTION_RETRY_ATTEMPTS = 1
TRANSCRIPTION_REQUEST_TIMEOUT_SECONDS = 120
TRANSCRIPTION_WATCHDOG_TIMEOUT_SECONDS = 45
WEB_TRANSCRIPTION_TIMEOUT_SECONDS = 300
WEB_TRANSCRIPTION_RETRY_ATTEMPTS = 2
TRANSCRIPTION_FALLBACK_SUFFIX = "_fallback.flac"
RETRYABLE_GENERATION_ERROR_FRAGMENTS = (
    "stream interrupted or truncated",
    "stream interrupted",
    "response body closed",
    "connection closed",
)
TRANSCRIPTION_RESULT_DIRECTIVE = (
    "Критично: верни только саму расшифровку реплик этого фрагмента по порядку. "
    "Не добавляй вступление, резюме, заголовки разделов, блок «Контекст» или отдельные пояснения."
)

PROTOCOL_TIMELINE_STEPS = [
    ("convert_audio", "Файл конвертации в аудиоформат"),
    ("split_chunks", "Разделение по частям"),
    ("transcribe_chunks", "Подготовка и отправка в генеративную модель"),
    ("build_protocol", "Подготовка протокола"),
    ("done", "Готово"),
]


@dataclass(slots=True)
class ProtocolChunk:
    index: int
    start_sec: float
    end_sec: float
    path: Path

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _file_safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return slug or "protocol"


def _safe_filename(value: str) -> str:
    name = Path(value).name.strip()
    stem = re.sub(r"[^a-zA-Z0-9а-яА-Я._ -]+", "-", Path(name).stem).strip(" .-_") or "media"
    suffix = Path(name).suffix.lower()
    if not suffix:
        return stem
    clean_suffix = re.sub(r"[^a-z0-9.]+", "", suffix) or ".bin"
    return f"{stem}{clean_suffix}"


def _format_clock(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _chunk_label(chunk: ProtocolChunk) -> str:
    return f"{_format_clock(chunk.start_sec)} - {_format_clock(chunk.end_sec)}"


def _plural_chunks(count: int) -> str:
    mod10 = count % 10
    mod100 = count % 100
    if mod10 == 1 and mod100 != 11:
        return "фрагмент"
    if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        return "фрагмента"
    return "фрагментов"


class ProtocolAgentService:
    def __init__(
        self,
        settings: Settings,
        direct_service: GeminiProxyService | None = None,
        web_runner: GeminiWebRunner | None = None,
    ) -> None:
        self.settings = settings
        self.direct_service = direct_service
        self.web_runner = web_runner
        self._run_lock = asyncio.Lock()
        self._timeline_tracker = TimelineTracker(_now_iso)

    def agent_summary(self) -> AgentSummary:
        return AgentSummary(
            id=DEFAULT_AGENT_ID,
            name="Протокол",
            description="Загружает запись встречи, режет ее на аудиофрагменты и формирует итоговый протокол.",
            kind="media-report",
        )

    def _config_path(self) -> Path:
        return self.settings.agents_root() / f"{DEFAULT_AGENT_ID}.json"

    def _reports_root(self) -> Path:
        root = self.settings.downloads_root() / "protocol-reports"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _latest_report_path(self) -> Path:
        return self._reports_root() / "latest.json"

    def _ensure_dirs(self) -> None:
        self.settings.agents_root().mkdir(parents=True, exist_ok=True)
        self._reports_root()

    def _default_config(self) -> ProtocolAgentConfig:
        return ProtocolAgentConfig(
            analysis_prompt=DEFAULT_ANALYSIS_PROMPT,
            protocol_prompt=DEFAULT_PROTOCOL_PROMPT,
            updated_at=_now_iso(),
        )

    def _write_config(self, config: ProtocolAgentConfig) -> None:
        self._config_path().write_text(
            json.dumps(config.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _upgrade_config(self, config: ProtocolAgentConfig) -> ProtocolAgentConfig:
        updated = config
        changed = False
        if config.analysis_prompt.strip() == LEGACY_ANALYSIS_PROMPT.strip():
            updated = updated.model_copy(update={"analysis_prompt": DEFAULT_ANALYSIS_PROMPT})
            changed = True
        if updated.protocol_prompt.strip() == LEGACY_PROTOCOL_PROMPT.strip():
            updated = updated.model_copy(update={"protocol_prompt": DEFAULT_PROTOCOL_PROMPT})
            changed = True
        if not changed:
            return updated
        updated = updated.model_copy(update={"updated_at": _now_iso()})
        self._write_config(updated)
        return updated

    def load_config(self) -> ProtocolAgentConfig:
        self._ensure_dirs()
        target = self._config_path()
        if not target.is_file():
            config = self._default_config()
            self._write_config(config)
            return config
        """
            GenerateRequest(
                prompt=full_prompt,
                temporary=False,
            ),
            empty_text_detail="Gemini завершил генерацию без текста итогового протокола.",
            failure_prefix="Не удалось сформировать итоговый протокол",
        )
        """
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            config = ProtocolAgentConfig.model_validate(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"Конфиг агента протокола поврежден: {exc}") from exc
        return self._upgrade_config(config)

    async def update_config(self, request: UpdateProtocolAgentConfigRequest) -> ProtocolAgentConfig:
        current = self.load_config()
        updated = current.model_copy(
            update={
                "analysis_prompt": request.analysis_prompt.strip(),
                "protocol_prompt": request.protocol_prompt.strip(),
                "updated_at": _now_iso(),
            }
        )
        self._write_config(updated)
        return updated

    def state(self) -> ProtocolReportState:
        payload = self._read_latest_report()
        if payload is None:
            return ProtocolReportState(ready=False)
        return ProtocolReportState(
            ready=True,
            title=payload.get("title"),
            created_at=payload.get("createdAt"),
            source_name=payload.get("sourceName"),
            source_mime_type=payload.get("sourceMimeType"),
            processing_strategy=payload.get("processingStrategy"),
            preprocessing_message=payload.get("preprocessingMessage"),
            document_name=payload.get("documentName"),
            document_url=payload.get("documentUrl"),
            transcript_name=payload.get("transcriptDocumentName"),
            transcript_url=payload.get("transcriptDocumentUrl"),
            chunk_count=payload.get("chunkCount"),
            duration_seconds=payload.get("durationSec"),
        )

    def reset_state(self) -> ProtocolResetResponse:
        target = self._latest_report_path()
        cleared = False
        if target.exists():
            target.unlink()
            cleared = True
        return ProtocolResetResponse(
            ok=True,
            cleared=cleared,
            message="Состояние агента протокола очищено." if cleared else "Состояние агента протокола уже было пустым.",
        )

    async def run(self, upload: UploadFile) -> ProtocolRunResponse:
        if self.direct_service is None:
            raise HTTPException(status_code=500, detail="Прямой Gemini-клиент не настроен для агента протокола.")

        config = self.load_config()
        filename = _safe_filename(upload.filename or "meeting-recording")
        source_mime_type = self._detect_media_type(filename, upload.content_type)

        stamp = datetime.now(self.settings.timezone()).strftime("%Y%m%d_%H%M%S")
        run_dir = self._reports_root() / f"{stamp}_{_file_safe_slug(Path(filename).stem)}"
        run_dir.mkdir(parents=True, exist_ok=True)

        source_dir = run_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        source_path = source_dir / filename

        try:
            total_bytes = await self._save_upload(upload, source_path)
        finally:
            await upload.close()

        if total_bytes <= 0:
            raise HTTPException(status_code=400, detail="Загруженный файл пустой.")

        normalized_audio_path, processing_strategy, duration_sec = await self._prepare_audio(
            source_path,
            source_mime_type,
            run_dir,
        )
        chunks = await self._split_audio(normalized_audio_path, duration_sec, run_dir)
        chunk_results = await self._transcribe_chunks(
            analysis_prompt=config.analysis_prompt,
            source_name=filename,
            source_mime_type=source_mime_type,
            processing_strategy=processing_strategy,
            duration_sec=duration_sec,
            chunks=chunks,
        )
        merged_transcript_text = self._merge_chunk_transcripts(
            source_name=filename,
            duration_sec=duration_sec,
            chunk_results=chunk_results,
        )

        chunk_manifest = {
            "sourceName": filename,
            "durationSec": duration_sec,
            "chunkCount": len(chunk_results),
            "chunks": [
                {
                    "index": item["index"],
                    "startSec": item["startSec"],
                    "endSec": item["endSec"],
                    "durationSec": item["durationSec"],
                    "rangeLabel": item["rangeLabel"],
                    "audioPath": item["audioPath"],
                    "transcriptPath": item["transcriptPath"],
                }
                for item in chunk_results
            ],
        }

        preprocessing_message = self._build_preprocessing_message(
            source_mime_type=source_mime_type,
            duration_sec=duration_sec,
            chunks=chunks,
        )
        protocol_prompt = (
            f"{config.protocol_prompt.strip()}\n\n"
            "Контекст запуска:\n"
            f"- Имя файла: {filename}\n"
            f"- MIME-тип исходника: {source_mime_type}\n"
            f"- Стратегия обработки: {processing_strategy}\n"
            f"- Общая длительность записи: {_format_clock(duration_sec)}\n"
            f"- Количество аудиофрагментов: {len(chunk_results)}\n"
            "- Ниже передана объединенная транскрибация, собранная по нескольким аудиофрагментам.\n"
            "- Если на границах фрагментов есть обрывы или повторяющиеся куски реплик, не дублируй их в выводах.\n"
        )
        report_text = await self._generate_protocol(protocol_prompt, merged_transcript_text)
        generation_message = (
            f"Протокол сформирован по объединенной транскрибации из {len(chunk_results)} "
            f"{_plural_chunks(len(chunk_results))}."
        )
        if self.web_runner is not None:
            generation_message = f"{generation_message} Расшифровка аудио выполнена через Gemini web runner."

        (run_dir / "transcription_prompt_template.txt").write_text(config.analysis_prompt.strip(), encoding="utf-8")
        (run_dir / "chunk_manifest.json").write_text(
            json.dumps(chunk_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (run_dir / "merged_transcript.md").write_text(merged_transcript_text, encoding="utf-8")
        (run_dir / "working_notes.md").write_text(merged_transcript_text, encoding="utf-8")
        (run_dir / "protocol_prompt.txt").write_text(protocol_prompt, encoding="utf-8")
        (run_dir / "protocol.md").write_text(report_text, encoding="utf-8")

        created_at = _now_iso()
        title = self._extract_report_title(report_text)
        transcript_title = self._extract_report_title(merged_transcript_text)
        transcript_docx_path = self._render_docx(
            merged_transcript_text,
            run_dir / "transcript.docx",
            transcript_title,
            [
                ("Дата генерации", datetime.now(self.settings.timezone()).strftime("%Y-%m-%d %H:%M")),
                ("Исходный файл", filename),
                ("MIME-тип", source_mime_type),
                ("Стратегия обработки", processing_strategy),
                ("Общая длительность", _format_clock(duration_sec)),
                ("Количество фрагментов", str(len(chunk_results))),
            ],
        )
        docx_path = self._render_docx(
            report_text,
            run_dir / "protocol.docx",
            title,
            [
                ("Дата генерации", datetime.now(self.settings.timezone()).strftime("%Y-%m-%d %H:%M")),
                ("Исходный файл", filename),
                ("MIME-тип", source_mime_type),
                ("Стратегия обработки", processing_strategy),
                ("Общая длительность", _format_clock(duration_sec)),
                ("Количество фрагментов", str(len(chunk_results))),
            ],
        )
        relative = docx_path.relative_to(self.settings.downloads_root()).as_posix()
        transcript_relative = transcript_docx_path.relative_to(self.settings.downloads_root()).as_posix()

        payload = {
            "agentId": DEFAULT_AGENT_ID,
            "agentName": self.agent_summary().name,
            "createdAt": created_at,
            "title": title,
            "sourceName": filename,
            "sourceMimeType": source_mime_type,
            "sourcePath": str(source_path),
            "preparedPath": str(normalized_audio_path),
            "processingStrategy": processing_strategy,
            "preprocessingMessage": preprocessing_message,
            "durationSec": duration_sec,
            "chunkCount": len(chunk_results),
            "chunkManifestPath": str(run_dir / "chunk_manifest.json"),
            "analysisPath": str(run_dir / "merged_transcript.md"),
            "transcriptMarkdownPath": str(run_dir / "merged_transcript.md"),
            "protocolPath": str(run_dir / "protocol.md"),
            "reportText": report_text,
            "documentName": docx_path.name,
            "documentPath": str(docx_path),
            "documentUrl": f"/downloads/{relative}",
            "transcriptDocumentName": transcript_docx_path.name,
            "transcriptDocumentPath": str(transcript_docx_path),
            "transcriptDocumentUrl": f"/downloads/{transcript_relative}",
        }
        (run_dir / "protocol_payload.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_latest_report(payload)

        n8n_roundtrip_ok, n8n_roundtrip_message = await self._send_report_to_n8n(
            title=title,
            report_text=report_text,
            docx_path=docx_path,
            transcript_text=merged_transcript_text,
            transcript_docx_path=transcript_docx_path,
            source_name=filename,
            source_mime_type=source_mime_type,
            processing_strategy=processing_strategy,
        )

        return ProtocolRunResponse(
            ok=True,
            agent_name=self.agent_summary().name,
            source_name=filename,
            source_mime_type=source_mime_type,
            processing_strategy=processing_strategy,
            preprocessing_message=preprocessing_message,
            generation_method="direct",
            generation_message=generation_message,
            report_text=report_text,
            document_name=docx_path.name,
            document_path=str(docx_path),
            document_url=f"/downloads/{relative}",
            transcript_name=transcript_docx_path.name,
            transcript_path=str(transcript_docx_path),
            transcript_url=f"/downloads/{transcript_relative}",
            n8n_roundtrip_ok=n8n_roundtrip_ok,
            n8n_roundtrip_message=n8n_roundtrip_message,
            created_at=created_at,
            title=title,
            chunk_count=len(chunk_results),
            duration_seconds=duration_sec,
        )

    async def _save_upload(self, upload: UploadFile, target: Path) -> int:
        total_bytes = 0
        with target.open("wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                total_bytes += len(chunk)
        return total_bytes

    def _detect_media_type(self, filename: str, reported_type: str | None) -> str:
        content_type = (reported_type or "").strip().lower()
        if content_type and content_type != "application/octet-stream":
            if content_type.startswith("audio/") or content_type.startswith("video/"):
                return content_type

        guessed, _ = mimetypes.guess_type(filename)
        if guessed and (guessed.startswith("audio/") or guessed.startswith("video/")):
            return guessed

        suffix = Path(filename).suffix.lower()
        if suffix in AUDIO_EXTENSIONS:
            return AUDIO_EXTENSIONS[suffix]
        if suffix in VIDEO_EXTENSIONS:
            return VIDEO_EXTENSIONS[suffix]

        raise HTTPException(
            status_code=400,
            detail="Агент протокола принимает только аудио- и видеофайлы.",
        )

    def _resolve_media_tools(self) -> tuple[str, str]:
        ffmpeg_command = self._resolve_media_binary("ffmpeg")
        ffprobe_command = self._resolve_media_binary("ffprobe")
        if ffmpeg_command and ffprobe_command:
            return ffmpeg_command, ffprobe_command
        missing: list[str] = []
        if not ffmpeg_command:
            missing.append("ffmpeg")
        if not ffprobe_command:
            missing.append("ffprobe")
        raise HTTPException(
            status_code=500,
            detail=(
                "Для агента протокола нужны установленные ffmpeg и ffprobe. "
                f"Сейчас отсутствует: {', '.join(missing)}."
            ),
        )

    def _resolve_media_binary(self, name: str) -> str | None:
        direct = shutil.which(name)
        if direct:
            return direct

        local_app_data = Path(os.environ.get("LOCALAPPDATA", "")).expanduser()
        if local_app_data:
            packages_root = local_app_data / "Microsoft" / "WinGet" / "Packages"
            if packages_root.is_dir():
                patterns = [
                    f"*/*/bin/{name}.exe",
                    f"*/*/{name}.exe",
                ]
                for pattern in patterns:
                    candidates = sorted(
                        packages_root.glob(pattern),
                        key=lambda item: item.stat().st_mtime if item.exists() else 0,
                        reverse=True,
                    )
                    for candidate in candidates:
                        if candidate.is_file():
                            return str(candidate)
        return None

    async def _run_subprocess(self, command: list[str], detail: str) -> subprocess.CompletedProcess[str]:
        try:
            return await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"{detail}: {exc}") from exc

    def _process_error(self, result: subprocess.CompletedProcess[str], fallback: str) -> str:
        message = (result.stderr or result.stdout or "").strip()
        if not message:
            return fallback
        compact = re.sub(r"\s+", " ", message)
        return compact[:400]

    async def _prepare_audio(
        self,
        source_path: Path,
        source_mime_type: str,
        run_dir: Path,
    ) -> tuple[Path, str, float]:
        ffmpeg_command, ffprobe_command = self._resolve_media_tools()
        prepared_dir = run_dir / "prepared"
        prepared_dir.mkdir(parents=True, exist_ok=True)
        normalized_audio_path = prepared_dir / "normalized_audio.mp3"

        command = [
            ffmpeg_command,
            "-y",
            "-i",
            str(source_path),
        ]
        if source_mime_type.startswith("video/"):
            command.extend(["-vn"])
        command.extend(
            [
                "-ac",
                str(NORMALIZED_AUDIO_CHANNELS),
                "-ar",
                str(NORMALIZED_AUDIO_SAMPLE_RATE),
                "-b:a",
                NORMALIZED_AUDIO_BITRATE,
                str(normalized_audio_path),
            ]
        )
        result = await self._run_subprocess(command, "Не удалось нормализовать аудио для протокола")
        if result.returncode != 0 or not normalized_audio_path.is_file():
            raise HTTPException(
                status_code=502,
                detail=(
                    "Не удалось подготовить аудиодорожку для протокола: "
                    f"{self._process_error(result, 'ffmpeg завершился с ошибкой.')}"
                ),
            )

        duration_sec = await self._probe_duration(ffprobe_command, normalized_audio_path)
        if duration_sec <= 0:
            raise HTTPException(status_code=502, detail="ffprobe вернул некорректную длительность аудио.")

        processing_strategy = "video-to-audio-chunks" if source_mime_type.startswith("video/") else "audio-normalized-chunks"
        return normalized_audio_path, processing_strategy, duration_sec

    async def _probe_duration(self, ffprobe_command: str, source_path: Path) -> float:
        command = [
            ffprobe_command,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source_path),
        ]
        result = await self._run_subprocess(command, "Не удалось получить длительность аудио")
        if result.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Не удалось получить длительность аудио: "
                    f"{self._process_error(result, 'ffprobe завершился с ошибкой.')}"
                ),
            )
        try:
            return float((result.stdout or "").strip())
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="ffprobe вернул некорректный формат длительности.") from exc

    def _build_chunk_windows(self, duration_sec: float) -> list[tuple[float, float]]:
        if duration_sec <= SINGLE_CHUNK_MAX_SECONDS:
            return [(0.0, duration_sec)]

        chunk_count = max(2, round(duration_sec / TARGET_CHUNK_SECONDS))
        while duration_sec / chunk_count > HARD_CHUNK_MAX_SECONDS:
            chunk_count += 1

        total_ms = max(1, int(round(duration_sec * 1000)))
        base_ms = total_ms // chunk_count
        remainder_ms = total_ms % chunk_count
        windows: list[tuple[float, float]] = []
        start_ms = 0
        for index in range(chunk_count):
            segment_ms = base_ms + (1 if index < remainder_ms else 0)
            end_ms = total_ms if index == chunk_count - 1 else start_ms + segment_ms
            windows.append((start_ms / 1000, end_ms / 1000))
            start_ms = end_ms
        return windows

    async def _split_audio(self, normalized_audio_path: Path, duration_sec: float, run_dir: Path) -> list[ProtocolChunk]:
        ffmpeg_command, _ = self._resolve_media_tools()
        chunks_root = run_dir / "chunks"
        chunks_root.mkdir(parents=True, exist_ok=True)

        chunks: list[ProtocolChunk] = []
        for index, (start_sec, end_sec) in enumerate(self._build_chunk_windows(duration_sec), start=1):
            chunk_dir = chunks_root / f"chunk_{index:02d}"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            chunk_path = chunk_dir / "audio.mp3"
            duration_value = max(0.1, end_sec - start_sec)
            command = [
                ffmpeg_command,
                "-y",
                "-ss",
                f"{start_sec:.3f}",
                "-i",
                str(normalized_audio_path),
                "-t",
                f"{duration_value:.3f}",
                "-ac",
                str(NORMALIZED_AUDIO_CHANNELS),
                "-ar",
                str(NORMALIZED_AUDIO_SAMPLE_RATE),
                "-b:a",
                NORMALIZED_AUDIO_BITRATE,
                str(chunk_path),
            ]
            result = await self._run_subprocess(command, "Не удалось нарезать аудио на фрагменты")
            if result.returncode != 0 or not chunk_path.is_file():
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Не удалось подготовить один из аудиофрагментов: "
                        f"{self._process_error(result, 'ffmpeg завершился с ошибкой при нарезке.')}"
                    ),
                )
            chunks.append(
                ProtocolChunk(
                    index=index,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    path=chunk_path,
                )
            )
        return chunks

    def _build_chunk_prompt(
        self,
        *,
        analysis_prompt: str,
        source_name: str,
        source_mime_type: str,
        processing_strategy: str,
        duration_sec: float,
        chunk: ProtocolChunk,
        total_chunks: int,
    ) -> str:
        lines = [
            f"{analysis_prompt.strip()}",
            "",
            "Контекст фрагмента:",
            f"- Исходный файл: {source_name}",
            f"- MIME-тип исходника: {source_mime_type}",
            f"- Стратегия обработки: {processing_strategy}",
            f"- Общая длительность записи: {_format_clock(duration_sec)}",
            f"- Номер фрагмента: {chunk.index} из {total_chunks}",
            f"- Диапазон фрагмента в записи: {_chunk_label(chunk)}",
            f"- Длительность фрагмента: {_format_clock(chunk.duration_sec)}",
            "- Расшифровывай только этот фрагмент и не пытайся восстановить другие части записи.",
            f"- {TRANSCRIPTION_RESULT_DIRECTIVE}",
            "- Каждую новую реплику начинай с новой строки. При необходимости помечай говорящих как «Спикер 1», «Спикер 2».",
        ]
        return "\n".join(lines) + "\n"

    async def _transcribe_chunks(
        self,
        *,
        analysis_prompt: str,
        source_name: str,
        source_mime_type: str,
        processing_strategy: str,
        duration_sec: float,
        chunks: list[ProtocolChunk],
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        total_chunks = len(chunks)
        for chunk in chunks:
            prompt = self._build_chunk_prompt(
                analysis_prompt=analysis_prompt,
                source_name=source_name,
                source_mime_type=source_mime_type,
                processing_strategy=processing_strategy,
                duration_sec=duration_sec,
                chunk=chunk,
                total_chunks=total_chunks,
            )
            transcript_text = await self._generate_chunk_transcript_resilient(prompt, chunk.path)
            transcript_path = chunk.path.with_name("transcript.md")
            prompt_path = chunk.path.with_name("prompt.txt")
            metadata_path = chunk.path.with_name("metadata.json")
            prompt_path.write_text(prompt, encoding="utf-8")
            transcript_path.write_text(transcript_text, encoding="utf-8")
            metadata_path.write_text(
                json.dumps(
                    {
                        "index": chunk.index,
                        "startSec": chunk.start_sec,
                        "endSec": chunk.end_sec,
                        "durationSec": chunk.duration_sec,
                        "rangeLabel": _chunk_label(chunk),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            results.append(
                {
                    "index": chunk.index,
                    "startSec": chunk.start_sec,
                    "endSec": chunk.end_sec,
                    "durationSec": chunk.duration_sec,
                    "rangeLabel": _chunk_label(chunk),
                    "audioPath": str(chunk.path),
                    "transcriptPath": str(transcript_path),
                    "transcriptText": transcript_text,
                }
            )
        return results

    def _is_retryable_generation_error(self, exc: Exception) -> bool:
        message = str(exc).strip().lower()
        return bool(message) and any(
            fragment in message for fragment in RETRYABLE_GENERATION_ERROR_FRAGMENTS
        )

    def _is_retryable_generation_http_exception(self, exc: HTTPException) -> bool:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return self._is_retryable_generation_error(RuntimeError(detail))

    async def _prepare_transcription_fallback_audio(self, source_path: Path) -> Path:
        ffmpeg_command, _ = self._resolve_media_tools()
        fallback_path = source_path.with_name(f"{source_path.stem}{TRANSCRIPTION_FALLBACK_SUFFIX}")
        command = [
            ffmpeg_command,
            "-y",
            "-i",
            str(source_path),
            "-ac",
            str(NORMALIZED_AUDIO_CHANNELS),
            "-ar",
            str(NORMALIZED_AUDIO_SAMPLE_RATE),
            "-c:a",
            "flac",
            str(fallback_path),
        ]
        result = await self._run_subprocess(command, "Не удалось переподготовить аудиофрагмент для повторной расшифровки")
        if result.returncode != 0 or not fallback_path.is_file():
            raise HTTPException(
                status_code=502,
                detail=(
                    "Не удалось переподготовить аудиофрагмент для повторной расшифровки: "
                    f"{self._process_error(result, 'ffmpeg завершился с ошибкой.')}"
                ),
            )
        return fallback_path

    async def _generate_chunk_transcript_via_web(self, prompt: str, source_path: Path) -> str:
        if self.web_runner is None:
            raise RuntimeError("Gemini web runner is not configured for protocol transcription.")

        last_error: Exception | None = None
        for attempt in range(1, WEB_TRANSCRIPTION_RETRY_ATTEMPTS + 1):
            try:
                result = await self.web_runner.run(
                    WebGenerateRequest(
                        prompt=prompt,
                        mode="pro",
                        headless=True,
                        file_path=str(source_path),
                        timeout_sec=WEB_TRANSCRIPTION_TIMEOUT_SECONDS,
                        capture_label=f"protocol-{source_path.stem}-attempt-{attempt}",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= WEB_TRANSCRIPTION_RETRY_ATTEMPTS:
                    break
                continue

            text = (result.assistant_text or "").strip()
            if text and "Ваш запрос" not in text and "Ответ Gemini" not in text:
                return text

            last_error = HTTPException(
                status_code=502,
                detail="Gemini web runner завершился без текста расшифровки аудиофрагмента.",
            )
            if attempt >= WEB_TRANSCRIPTION_RETRY_ATTEMPTS:
                break

        if isinstance(last_error, HTTPException):
            raise last_error
        if last_error is not None:
            raise HTTPException(
                status_code=502,
                detail=f"Gemini web runner не смог расшифровать аудиофрагмент: {last_error}",
            ) from last_error
        raise HTTPException(
            status_code=502,
            detail="Gemini web runner завершился без текста расшифровки аудиофрагмента.",
        )

    async def _generate_text_with_retry(
        self,
        request: GenerateRequest,
        *,
        empty_text_detail: str,
        failure_prefix: str,
        retry_attempts: int = GENERATION_RETRY_ATTEMPTS,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(1, retry_attempts + 1):
            try:
                result = await self.direct_service.generate(request)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                retryable = self._is_retryable_generation_error(exc)
                if not retryable or attempt >= retry_attempts:
                    suffix = (
                        f" после {retry_attempts} попыток"
                        if retryable and retry_attempts > 1 and attempt >= retry_attempts
                        else ""
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=f"{failure_prefix}{suffix}: {exc}",
                    ) from exc
                await asyncio.sleep(GENERATION_RETRY_DELAY_SECONDS * attempt)
                continue

            text = (result.text or "").strip()
            if text:
                return text
            last_error = RuntimeError(empty_text_detail)
            if attempt >= retry_attempts:
                break
            await asyncio.sleep(GENERATION_RETRY_DELAY_SECONDS * attempt)

        if last_error is not None:
            raise HTTPException(status_code=502, detail=str(last_error)) from last_error
        raise HTTPException(status_code=502, detail=empty_text_detail)

    async def _generate_chunk_transcript_resilient(self, prompt: str, source_path: Path) -> str:
        empty_text_detail = "Gemini не вернул расшифровку одного из аудиофрагментов."
        failure_prefix = "Не удалось получить расшифровку одного из аудиофрагментов"
        web_error: Exception | None = None

        if self.web_runner is not None:
            try:
                return await self._generate_chunk_transcript_via_web(prompt, source_path)
            except Exception as exc:  # noqa: BLE001
                web_error = exc

        try:
            return await self._generate_text_with_retry(
                GenerateRequest(
                    prompt=prompt,
                    files=[InputFile(path=str(source_path))],
                    temporary=True,
                    timeout_sec=TRANSCRIPTION_REQUEST_TIMEOUT_SECONDS,
                    watchdog_timeout_sec=TRANSCRIPTION_WATCHDOG_TIMEOUT_SECONDS,
                    disable_internal_retry=True,
                ),
                empty_text_detail=empty_text_detail,
                failure_prefix=failure_prefix,
                retry_attempts=TRANSCRIPTION_RETRY_ATTEMPTS,
            )
        except HTTPException as exc:
            if not self._is_retryable_generation_http_exception(exc):
                if web_error is not None:
                    raise HTTPException(
                        status_code=502,
                        detail=f"{failure_prefix}. Web: {web_error}. Direct: {exc.detail}",
                    ) from exc
                raise

        fallback_path = await self._prepare_transcription_fallback_audio(source_path)
        try:
            return await self._generate_text_with_retry(
                GenerateRequest(
                    prompt=prompt,
                    files=[InputFile(path=str(fallback_path))],
                    temporary=True,
                    timeout_sec=TRANSCRIPTION_REQUEST_TIMEOUT_SECONDS,
                    watchdog_timeout_sec=TRANSCRIPTION_WATCHDOG_TIMEOUT_SECONDS,
                    disable_internal_retry=True,
                ),
                empty_text_detail=empty_text_detail,
                failure_prefix=f"{failure_prefix} после повторной подготовки аудиофрагмента",
                retry_attempts=TRANSCRIPTION_RETRY_ATTEMPTS,
            )
        except HTTPException as exc:
            if web_error is not None:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"{failure_prefix}. Web: {web_error}. "
                        f"Direct fallback: {exc.detail}"
                    ),
                ) from exc
            raise

    async def _generate_chunk_transcript(self, prompt: str, source_path: Path) -> str:
        return await self._generate_chunk_transcript_resilient(prompt, source_path)

    def _merge_chunk_transcripts(
        self,
        *,
        source_name: str,
        duration_sec: float,
        chunk_results: list[dict[str, object]],
    ) -> str:
        parts = [
            "# Объединенная транскрибация встречи",
            "",
            f"- Исходный файл: {source_name}",
            f"- Общая длительность: {_format_clock(duration_sec)}",
            f"- Количество фрагментов: {len(chunk_results)}",
            "",
        ]
        for item in chunk_results:
            parts.append(f"## Фрагмент {item['index']} ({item['rangeLabel']})")
            parts.append(str(item["transcriptText"]).strip())
            parts.append("")
        return "\n".join(parts).strip() + "\n"

    def _build_preprocessing_message(
        self,
        *,
        source_mime_type: str,
        duration_sec: float,
        chunks: list[ProtocolChunk],
    ) -> str:
        source_label = "Видео" if source_mime_type.startswith("video/") else "Аудио"
        if len(chunks) == 1:
            return (
                f"{source_label} конвертировано в нормализованный MP3 "
                f"({_format_clock(duration_sec)}), деление на части не потребовалось."
            )

        average_length = duration_sec / len(chunks)
        durations = ", ".join(_format_clock(chunk.duration_sec) for chunk in chunks[:4])
        if len(chunks) > 4:
            durations = f"{durations} и еще {len(chunks) - 4}"
        return (
            f"{source_label} конвертировано в нормализованный MP3 и разбито на {len(chunks)} "
            f"{_plural_chunks(len(chunks))}. Длительности: {durations}. "
            f"Средняя длина фрагмента: {_format_clock(average_length)}."
        )

    async def _generate_protocol(self, prompt: str, merged_transcript_text: str) -> str:
        full_prompt = f"{prompt}\n\nОбъединенная транскрибация встречи:\n{merged_transcript_text}"
        return await self._generate_text_with_retry(
            GenerateRequest(
                prompt=full_prompt,
                temporary=True,
            ),
            empty_text_detail="Gemini не завершил сборку текста итогового протокола.",
            failure_prefix="Не удалось сформировать итоговый протокол",
        )

    def _extract_report_title(self, report_text: str) -> str:
        for line in report_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return "Протокол встречи"

    def _render_docx(
        self,
        report_text: str,
        output_path: Path,
        title: str,
        meta_lines: list[tuple[str, str]],
    ) -> Path:
        document = Document()
        document.core_properties.title = title
        document.add_heading(title, level=0)
        for label, value in meta_lines:
            paragraph = document.add_paragraph()
            paragraph.add_run(f"{label}: ").bold = True
            paragraph.add_run(value)
        for raw_line in report_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("# "):
                continue
            if line.startswith("## "):
                document.add_heading(line[3:].strip(), level=1)
            elif line.startswith("### "):
                document.add_heading(line[4:].strip(), level=2)
            elif re.match(r"^\d+\.\s+", line):
                document.add_paragraph(re.sub(r"^\d+\.\s+", "", line), style="List Number")
            elif line.startswith("- "):
                document.add_paragraph(line[2:].strip(), style="List Bullet")
            else:
                document.add_paragraph(line)
        document.save(output_path)
        return output_path

    def _write_latest_report(self, payload: dict[str, object]) -> None:
        self._latest_report_path().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_latest_report(self) -> dict[str, object] | None:
        target = self._latest_report_path()
        if not target.is_file():
            return None
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    async def _send_report_to_n8n(
        self,
        *,
        title: str,
        report_text: str,
        docx_path: Path,
        transcript_text: str,
        transcript_docx_path: Path,
        source_name: str,
        source_mime_type: str,
        processing_strategy: str,
    ) -> tuple[bool, str]:
        url = (self.settings.n8n_report_ingest_url or "").strip()
        if not url:
            return False, "Webhook обратной передачи в n8n пока не настроен."

        payload = {
            "agentId": DEFAULT_AGENT_ID,
            "agentName": self.agent_summary().name,
            "title": title,
            "reportText": report_text,
            "sourceName": source_name,
            "sourceMimeType": source_mime_type,
            "processingStrategy": processing_strategy,
            "documentName": docx_path.name,
            "documentMimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "documentBase64": base64.b64encode(docx_path.read_bytes()).decode("ascii"),
            "transcriptText": transcript_text,
            "transcriptDocumentName": transcript_docx_path.name,
            "transcriptDocumentMimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "transcriptDocumentBase64": base64.b64encode(transcript_docx_path.read_bytes()).decode("ascii"),
            "sentAt": _now_iso(),
        }
        try:
            async with httpx.AsyncClient(timeout=90.0, verify=self.settings.gemini_verify_ssl) as client:
                response = await client.post(url, json=payload, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            return False, f"Не удалось передать протокол в n8n: {exc}"
        if response.status_code >= 400:
            return False, f"n8n intake вернул ошибку {response.status_code}."
        return True, "Протокол отправлен в n8n для дальнейшей обработки."
