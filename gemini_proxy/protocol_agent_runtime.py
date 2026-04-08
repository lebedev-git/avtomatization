from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .markdown_docx import render_markdown_to_docx
from .protocol_agent import (
    DEFAULT_AGENT_ID,
    PROTOCOL_TIMELINE_STEPS,
    ProtocolAgentService as BaseProtocolAgentService,
    ProtocolChunk,
    TRANSCRIPTION_RESULT_DIRECTIVE,
    _chunk_label,
    _file_safe_slug,
    _format_clock,
    _now_iso,
    _plural_chunks,
    _safe_filename,
)
from .progress import TimelineTracker
from .schemas import ProcessingTimeline, ProtocolReportState, ProtocolResetResponse, ProtocolRunResponse

RESCUE_SUBCHUNK_TARGET_SECONDS = 30


class ProtocolAgentService(BaseProtocolAgentService):
    def __init__(self, settings, direct_service=None, web_runner=None) -> None:
        super().__init__(settings, direct_service, web_runner)
        self._run_lock = asyncio.Lock()
        self._timeline_tracker = TimelineTracker(_now_iso)

    def _timeline_from_state(self, latest: dict[str, object] | None = None) -> ProcessingTimeline:
        live = self._timeline_tracker.get(DEFAULT_AGENT_ID)
        if live is not None:
            return live
        stored = latest if latest is not None else self._read_latest_report()
        if isinstance(stored, dict) and isinstance(stored.get("timeline"), dict):
            return ProcessingTimeline.model_validate(stored["timeline"])
        if stored is not None:
            return self._timeline_tracker.complete_without_run(
                PROTOCOL_TIMELINE_STEPS,
                {step_id for step_id, _ in PROTOCOL_TIMELINE_STEPS},
                current_step_id="done",
                summary="Последний запуск завершен успешно.",
                updated_at=str(stored.get("createdAt") or ""),
            )
        return self._timeline_tracker.complete_without_run(PROTOCOL_TIMELINE_STEPS, set())

    def _render_docx(
        self,
        report_text: str,
        output_path: Path,
        title: str,
        meta_lines: list[tuple[str, str]],
    ) -> Path:
        return render_markdown_to_docx(
            report_text=report_text,
            output_path=output_path,
            title=title,
            meta_lines=meta_lines,
            source_notes=None,
        )

    def state(self) -> ProtocolReportState:
        payload = self._read_latest_report()
        if payload is None:
            return ProtocolReportState(ready=False, timeline=self._timeline_from_state(None))
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
            timeline=self._timeline_from_state(payload),
        )

    def reset_state(self) -> ProtocolResetResponse:
        target = self._latest_report_path()
        cleared = False
        if target.exists():
            target.unlink()
            cleared = True
        self._timeline_tracker.clear(DEFAULT_AGENT_ID)
        return ProtocolResetResponse(
            ok=True,
            cleared=cleared,
            message="Состояние агента протокола очищено." if cleared else "Состояние агента протокола уже было пустым.",
        )

    async def run(self, upload: UploadFile) -> ProtocolRunResponse:
        async with self._run_lock:
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
            current_step = "convert_audio"
            timeline_started = False

            try:
                self._timeline_tracker.start(
                    DEFAULT_AGENT_ID,
                    PROTOCOL_TIMELINE_STEPS,
                    summary="Формирую протокол встречи.",
                    first_message="Сохраняю исходный файл и привожу его к аудиоформату.",
                )
                timeline_started = True

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

                current_step = "split_chunks"
                self._timeline_tracker.advance(DEFAULT_AGENT_ID, current_step, message="Нарезаю аудио на рабочие фрагменты.")
                chunks = await self._split_audio(normalized_audio_path, duration_sec, run_dir)

                current_step = "transcribe_chunks"
                self._timeline_tracker.advance(
                    DEFAULT_AGENT_ID,
                    current_step,
                    message=f"Подготавливаю и отправляю в модель {len(chunks)} {_plural_chunks(len(chunks))}.",
                )
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

                current_step = "build_protocol"
                self._timeline_tracker.advance(DEFAULT_AGENT_ID, current_step, message="Собираю объединенную транскрибацию и готовлю итоговый протокол.")
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
                (run_dir / "chunk_manifest.json").write_text(json.dumps(chunk_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
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

                done_message = "Протокол и транскрибация готовы."
                if n8n_roundtrip_message and not n8n_roundtrip_ok:
                    done_message = f"{done_message} {n8n_roundtrip_message}"
                timeline = self._timeline_tracker.finish(DEFAULT_AGENT_ID, "done", message=done_message, summary=done_message)
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
                    "timeline": timeline.model_dump(),
                }
                (run_dir / "protocol_payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                self._write_latest_report(payload)
                self._timeline_tracker.clear(DEFAULT_AGENT_ID)

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
                    timeline=timeline,
                )
            except HTTPException as exc:
                if timeline_started:
                    self._timeline_tracker.fail(DEFAULT_AGENT_ID, current_step, message=str(exc.detail))
                raise
            except Exception as exc:
                if timeline_started:
                    self._timeline_tracker.fail(DEFAULT_AGENT_ID, current_step, message=str(exc))
                raise

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
        subchunk_index: int | None = None,
        subchunk_total: int | None = None,
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
        ]
        if subchunk_index is not None and subchunk_total is not None:
            lines.extend(
                [
                    f"- Номер подфрагмента: {subchunk_index} из {subchunk_total}",
                    "- Это аварийный повтор для проблемного фрагмента. Расшифровывай только этот подфрагмент.",
                ]
            )
        else:
            lines.append("- Расшифровывай только этот фрагмент и не пытайся восстанавливать другие части записи.")
        lines.extend(
            [
                f"- {TRANSCRIPTION_RESULT_DIRECTIVE}",
                "- Каждую новую реплику начинай с новой строки. При необходимости помечай говорящих как «Спикер 1», «Спикер 2».",
            ]
        )
        return "\n".join(lines) + "\n"

    def _build_rescue_chunk_windows(self, duration_sec: float) -> list[tuple[float, float]]:
        if duration_sec <= RESCUE_SUBCHUNK_TARGET_SECONDS:
            return [(0.0, duration_sec)]

        chunk_count = max(2, math.ceil(duration_sec / RESCUE_SUBCHUNK_TARGET_SECONDS))
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

    async def _split_chunk_for_rescue(self, chunk: ProtocolChunk) -> list[ProtocolChunk]:
        ffmpeg_command, _ = self._resolve_media_tools()
        rescue_root = chunk.path.parent / "rescue"
        rescue_root.mkdir(parents=True, exist_ok=True)

        rescue_chunks: list[ProtocolChunk] = []
        for index, (start_sec, end_sec) in enumerate(self._build_rescue_chunk_windows(chunk.duration_sec), start=1):
            sub_dir = rescue_root / f"part_{index:02d}"
            sub_dir.mkdir(parents=True, exist_ok=True)
            sub_path = sub_dir / "audio.mp3"
            duration_value = max(0.1, end_sec - start_sec)
            command = [
                ffmpeg_command,
                "-y",
                "-ss",
                f"{start_sec:.3f}",
                "-i",
                str(chunk.path),
                "-t",
                f"{duration_value:.3f}",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-b:a",
                "64k",
                str(sub_path),
            ]
            result = await self._run_subprocess(command, "Не удалось дополнительно нарезать проблемный фрагмент")
            if result.returncode != 0 or not sub_path.is_file():
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Не удалось дополнительно нарезать проблемный фрагмент: "
                        f"{self._process_error(result, 'ffmpeg завершился с ошибкой при аварийной нарезке.')}"
                    ),
                )
            rescue_chunks.append(
                ProtocolChunk(
                    index=index,
                    start_sec=chunk.start_sec + start_sec,
                    end_sec=chunk.start_sec + end_sec,
                    path=sub_path,
                )
            )
        return rescue_chunks

    async def _transcribe_chunk_with_rescue(
        self,
        *,
        prompt: str,
        chunk: ProtocolChunk,
        total_chunks: int,
        analysis_prompt: str,
        source_name: str,
        source_mime_type: str,
        processing_strategy: str,
        duration_sec: float,
    ) -> str:
        self._timeline_tracker.touch(
            DEFAULT_AGENT_ID,
            "transcribe_chunks",
            message=f"Фрагмент {chunk.index} из {total_chunks}: {_chunk_label(chunk)}.",
        )
        try:
            return await self._generate_chunk_transcript_resilient(prompt, chunk.path)
        except HTTPException as exc:
            if not self._is_retryable_generation_http_exception(exc) or chunk.duration_sec <= RESCUE_SUBCHUNK_TARGET_SECONDS:
                raise

        self._timeline_tracker.touch(
            DEFAULT_AGENT_ID,
            "transcribe_chunks",
            message=f"Фрагмент {chunk.index} из {total_chunks}: повторяю более мелкими частями.",
        )
        rescue_chunks = await self._split_chunk_for_rescue(chunk)
        rescue_total = len(rescue_chunks)
        rescue_parts: list[str] = []

        for rescue_chunk in rescue_chunks:
            self._timeline_tracker.touch(
                DEFAULT_AGENT_ID,
                "transcribe_chunks",
                message=(
                    f"Фрагмент {chunk.index} из {total_chunks}, "
                    f"подфрагмент {rescue_chunk.index} из {rescue_total}: {_chunk_label(rescue_chunk)}."
                ),
            )
            rescue_prompt = self._build_chunk_prompt(
                analysis_prompt=analysis_prompt,
                source_name=source_name,
                source_mime_type=source_mime_type,
                processing_strategy=processing_strategy,
                duration_sec=duration_sec,
                chunk=rescue_chunk,
                total_chunks=total_chunks,
                subchunk_index=rescue_chunk.index,
                subchunk_total=rescue_total,
            )
            rescue_text = await self._generate_chunk_transcript_resilient(rescue_prompt, rescue_chunk.path)
            rescue_prompt_path = rescue_chunk.path.with_name("prompt.txt")
            rescue_transcript_path = rescue_chunk.path.with_name("transcript.md")
            rescue_metadata_path = rescue_chunk.path.with_name("metadata.json")
            rescue_prompt_path.write_text(rescue_prompt, encoding="utf-8")
            rescue_transcript_path.write_text(rescue_text, encoding="utf-8")
            rescue_metadata_path.write_text(
                json.dumps(
                    {
                        "index": rescue_chunk.index,
                        "startSec": rescue_chunk.start_sec,
                        "endSec": rescue_chunk.end_sec,
                        "durationSec": rescue_chunk.duration_sec,
                        "rangeLabel": _chunk_label(rescue_chunk),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            rescue_parts.append(f"### Подфрагмент {rescue_chunk.index} ({_chunk_label(rescue_chunk)})\n{rescue_text.strip()}")

        return "\n\n".join(rescue_parts).strip()

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
            self._timeline_tracker.touch(
                DEFAULT_AGENT_ID,
                "transcribe_chunks",
                message=f"Фрагмент {chunk.index} из {total_chunks}: {_chunk_label(chunk)}.",
            )
            prompt = self._build_chunk_prompt(
                analysis_prompt=analysis_prompt,
                source_name=source_name,
                source_mime_type=source_mime_type,
                processing_strategy=processing_strategy,
                duration_sec=duration_sec,
                chunk=chunk,
                total_chunks=total_chunks,
            )
            transcript_text = await self._transcribe_chunk_with_rescue(
                prompt=prompt,
                chunk=chunk,
                total_chunks=total_chunks,
                analysis_prompt=analysis_prompt,
                source_name=source_name,
                source_mime_type=source_mime_type,
                processing_strategy=processing_strategy,
                duration_sec=duration_sec,
            )
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
