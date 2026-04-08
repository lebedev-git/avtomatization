from __future__ import annotations

import asyncio
import base64
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from fastapi import HTTPException
from gemini_webapi.constants import Model
import httpx

from .analytics_n8n_workflow import build_fetch_workflow_payload
from .config import Settings
from .markdown_docx import render_markdown_to_docx
from .notebooklm_service import NotebookLMService
from .progress import TimelineTracker
from .schemas import (
    AgentListResponse,
    AgentSummary,
    AnalyticsAgentConfig,
    AnalyticsBlockConfig,
    AnalyticsDateOption,
    AnalyticsDay1HistoryResponse,
    AnalyticsDay1RunRequest,
    AnalyticsDay1RunResponse,
    AnalyticsDay2HistoryResponse,
    AnalyticsDay2RunRequest,
    AnalyticsDay2RunResponse,
    AnalyticsInfographicRunRequest,
    AnalyticsInfographicRunResponse,
    AnalyticsInfographicState,
    AnalyticsResetResponse,
    AnalyticsReportState,
    AnalyticsSourceForm,
    AnalyticsSourceStatusItem,
    AnalyticsSourceStatusResponse,
    AnalyticsSummaryRunRequest,
    AnalyticsSummaryRunResponse,
    AnalyticsSummaryStateResponse,
    GenerateRequest,
    InputFile,
    ProcessingTimeline,
    UpdateAnalyticsAgentConfigRequest,
    WebGenerateRequest,
)
from .service import GeminiProxyService
from .web_runner import GeminiWebRunner

DEFAULT_AGENT_ID = "analytics-note"
SUMMARY_DIRECT_MODEL_NAME = Model.G_3_1_PRO.model_name
DAY1_ENTRY_FORM_URL = "https://forms.yandex.ru/cloud/69b447a8e010db3d6b505b69"
DAY1_EXIT_FORM_URL = "https://forms.yandex.ru/cloud/69b5762790fa7b47e155853c"
DAY2_FORM_URL = "https://forms.yandex.ru/cloud/69b5799f49af4761ee2057c6"

DEFAULT_DAY1_PROMPT = """Ты аналитик по организационному развитию и образовательным программам.

Подготовь аналитическую записку по первому дню строго на основе двух массивов ответов: входной и выходной анкеты.
Пиши только по данным из JSON, сопоставляй анкеты между собой и явно отмечай ограничения данных.
Верни результат в Markdown с разделами:
# Аналитическая записка по первому дню
## Контекст
## Что видно по входной анкете
## Что видно по выходной анкете
## Динамика и изменения первого дня
## Риски и ограничения
## Практические выводы
## Рекомендации"""

DEFAULT_DAY2_PROMPT = """Ты аналитик по цифровой трансформации и организационному развитию.

Подготовь аналитическую записку по второму дню строго на основе JSON с ответами анкеты второго дня.
Пиши только по данным из JSON, явно отмечай ограничения и верни Markdown со структурой:
# Аналитическая записка по второму дню
## Контекст и период анализа
## Ключевые наблюдения
## Интерпретация результатов
## Риски и ограничения
## Практические рекомендации
## Приоритетные следующие шаги"""

DEFAULT_SUMMARY_PROMPT = """Ты готовишь общую итоговую аналитику по программе на основе двух уже сформированных аналитических записок: первого и второго дня.
Используй только информацию из этих двух текстов, синтезируй ее и не пересказывай дословно.
Верни Markdown со структурой:
# Общая итоговая аналитика
## Контекст
## Общая картина по двум дням
## Ключевые устойчивые выводы
## Изменения между первым и вторым днем
## Риски и ограничения
## Рекомендации
## Следующие шаги"""

DEFAULT_INFOGRAPHIC_PROMPT = """Создай инфографику в NotebookLM на русском языке.
Используй Google Doc пользователя как визуальный бриф, готовую итоговую аналитику как фактическую основу, а описания общего фото и логотипа как визуальные ориентиры.
Не выдумывай факты. Если данные конфликтуют, приоритет у итоговой аналитики."""

MONTHS_GENITIVE = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
    "infographic": [
        ("prepare", "РџСЂРѕРІРµСЂРєР° РёС‚РѕРіРѕРІРѕР№ Р°РЅР°Р»РёС‚РёРєРё Рё РІС…РѕРґРЅС‹С… РґР°РЅРЅС‹С…"),
        ("describe", "РџРѕРґРіРѕС‚РѕРІРєР° РѕРїРёСЃР°РЅРёР№ РѕР±С‰РµРіРѕ С„РѕС‚Рѕ Рё Р»РѕРіРѕС‚РёРїР°"),
        ("notebook", "РЎРѕР·РґР°РЅРёРµ Р±Р»РѕРєРЅРѕС‚Р° NotebookLM"),
        ("sources", "Р”РѕР±Р°РІР»РµРЅРёРµ РёСЃС‚РѕС‡РЅРёРєРѕРІ РІ NotebookLM"),
        ("artifact", "Р—Р°РїСѓСЃРє РёРЅС„РѕРіСЂР°С„РёРєРё РІ NotebookLM"),
        ("done", "Р“РѕС‚РѕРІРѕ"),
    ],
    "infographic": [
        ("prepare", "Проверка итоговой аналитики и входных данных"),
        ("describe", "Подготовка описаний общего фото и логотипа"),
        ("notebook", "Создание блокнота NotebookLM"),
        ("sources", "Добавление источников в NotebookLM"),
        ("artifact", "Запуск инфографики в NotebookLM"),
        ("done", "Готово"),
    ],
}

MONTHS_GENITIVE = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

ANALYTICS_TIMELINE_STEPS: dict[str, list[tuple[str, str]]] = {
    "day1": [
        ("fetch", "Получение ответов первого дня"),
        ("prepare", "Фиксация даты и подготовка выборки"),
        ("generate", "Подготовка и отправка в модель"),
        ("convert", "Сборка документа из Markdown"),
        ("sync", "Передача результата в n8n"),
        ("done", "Готово"),
    ],
    "day2": [
        ("fetch", "Получение ответов второго дня"),
        ("prepare", "Фиксация даты и подготовка выборки"),
        ("generate", "Подготовка и отправка в модель"),
        ("convert", "Сборка документа из Markdown"),
        ("sync", "Передача результата в n8n"),
        ("done", "Готово"),
    ],
    "summary": [
        ("prepare", "Проверка готовых текстов и зависимостей"),
        ("generate", "Подготовка и отправка в модель"),
        ("convert", "Сборка документа из Markdown"),
        ("sync", "Передача результата в n8n"),
        ("done", "Готово"),
    ],
}
ANALYTICS_TIMELINE_STEPS["infographic"] = [
    ("prepare", "Проверка итоговой аналитики и входных данных"),
    ("describe", "Подготовка описаний общего фото и логотипа"),
    ("notebook", "Создание блокнота NotebookLM"),
    ("sources", "Добавление источников в NotebookLM"),
    ("artifact", "Запуск инфографики в NotebookLM"),
    ("done", "Готово"),
]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _extract_survey_id(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"([0-9a-f]{24})", value)
    return match.group(1) if match else None


def _parse_created(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _clean_string(value: str) -> str:
    return value.replace("\r", "").replace("\n", " ").strip()


def _clean_value(value: Any) -> Any:
    if isinstance(value, str):
        return _clean_string(value)
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _clean_value(item) for key, item in value.items()}
    return value


def _format_russian_date(value: date) -> str:
    return f"{value.day} {MONTHS_GENITIVE[value.month]} {value.year}"


def _file_safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return slug or "report"


def _compact_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class AnalyticsAgentService:
    def __init__(
        self,
        settings: Settings,
        web_runner: GeminiWebRunner,
        direct_service: GeminiProxyService | None = None,
        notebooklm_service: NotebookLMService | None = None,
    ) -> None:
        self.settings = settings
        self.web_runner = web_runner
        self.direct_service = direct_service
        self.notebooklm_service = notebooklm_service
        self._run_lock = asyncio.Lock()
        self._timeline_tracker = TimelineTracker(_now_iso)

    def agent_summary(self) -> AgentSummary:
        return AgentSummary(
            id=DEFAULT_AGENT_ID,
            name="Аналитика",
            description="Собирает записки по первому и второму дню и формирует общий итог.",
            kind="text-report",
        )

    def agent_summary(self) -> AgentSummary:
        return AgentSummary(
            id=DEFAULT_AGENT_ID,
            name="Аналитика",
            description="Собирает документы первого и второго дня, итоговую аналитику и отдельный блок инфографики.",
            kind="text-report",
        )

    def list_agents(self) -> AgentListResponse:
        return AgentListResponse(agents=[self.agent_summary()])

    def _config_path(self) -> Path:
        return self.settings.agents_root() / f"{DEFAULT_AGENT_ID}.json"

    def _reports_root(self, block_id: str | None = None) -> Path:
        root = self.settings.downloads_root() / "analytics-reports"
        if block_id:
            root = root / block_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _cache_path(self, block_id: str) -> Path:
        target = self.settings.n8n_inbox_root() / f"{block_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _latest_report_path(self, block_id: str) -> Path:
        return self._reports_root(block_id) / "latest.json"

    def _clear_latest_report(self, block_id: str) -> bool:
        target = self._latest_report_path(block_id)
        if not target.exists():
            return False
        target.unlink()
        return True

    def _ensure_dirs(self) -> None:
        self.settings.agents_root().mkdir(parents=True, exist_ok=True)
        self.settings.n8n_inbox_root().mkdir(parents=True, exist_ok=True)
        for block_id in ("day1", "day2", "summary", "infographic"):
            self._reports_root(block_id)

    def _timeline_steps(self, block_id: str) -> list[tuple[str, str]]:
        return ANALYTICS_TIMELINE_STEPS[block_id]

    def _timeline_from_state(
        self,
        block_id: str,
        latest: dict[str, Any] | None = None,
        *,
        summary: str | None = None,
    ) -> ProcessingTimeline:
        live = self._timeline_tracker.get(block_id)
        if live is not None:
            return live
        stored = latest if latest is not None else self._read_latest_report(block_id)
        if isinstance(stored, dict) and isinstance(stored.get("timeline"), dict):
            return ProcessingTimeline.model_validate(stored["timeline"])
        if stored is not None:
            return self._timeline_tracker.complete_without_run(
                self._timeline_steps(block_id),
                {step_id for step_id, _ in self._timeline_steps(block_id)},
                current_step_id="done",
                summary=summary or "Последний запуск завершен успешно.",
                updated_at=stored.get("createdAt"),
            )
        return self._timeline_tracker.complete_without_run(
            self._timeline_steps(block_id),
            set(),
            summary=summary,
        )

    def _default_forms(self) -> dict[str, list[AnalyticsSourceForm]]:
        day1_entry_id = _extract_survey_id(DAY1_ENTRY_FORM_URL)
        day1_exit_id = _extract_survey_id(DAY1_EXIT_FORM_URL)
        day2_id = _extract_survey_id(DAY2_FORM_URL)
        if not day1_entry_id or not day1_exit_id or not day2_id:
            raise RuntimeError("Не удалось извлечь surveyId для форм аналитики.")
        return {
            "day1": [
                AnalyticsSourceForm(id="day1-entry", name="Входная анкета первого дня", url=DAY1_ENTRY_FORM_URL, survey_id=day1_entry_id),
                AnalyticsSourceForm(id="day1-exit", name="Выходная анкета первого дня", url=DAY1_EXIT_FORM_URL, survey_id=day1_exit_id),
                AnalyticsBlockConfig(id="infographic", name="Инфографика", description="Создает отдельный блокнот NotebookLM и запускает инфографику по Google Doc, итоговой аналитике и визуальным ориентирам.", mode="notebooklm-infographic", system_prompt=DEFAULT_INFOGRAPHIC_PROMPT, source_forms=[]),
            ],
            "day2": [
                AnalyticsSourceForm(id="day2-main", name="Анкета второго дня", url=DAY2_FORM_URL, survey_id=day2_id)
            ],
            "summary": [],
            "infographic": [],
        }

    def _default_config(self) -> AnalyticsAgentConfig:
        forms = self._default_forms()
        return AnalyticsAgentConfig(
            blocks=[
                AnalyticsBlockConfig(id="day1", name="Первый день", description="Объединяет входную и выходную анкеты первого дня.", mode="single-date", system_prompt=DEFAULT_DAY1_PROMPT, source_forms=forms["day1"]),
                AnalyticsBlockConfig(id="day2", name="Второй день", description="Готовит записку по анкете второго дня по одной дате или диапазону дат.", mode="date-range", system_prompt=DEFAULT_DAY2_PROMPT, source_forms=forms["day2"]),
                AnalyticsBlockConfig(id="summary", name="Общая итоговая аналитика", description="Сводит готовые записки первого и второго дня.", mode="reports-only", system_prompt=DEFAULT_SUMMARY_PROMPT, source_forms=[]),
            ],
            synced_to_n8n=False,
            sync_message="Конфиг создан локально. Синхронизация с n8n выполнится при сохранении.",
            updated_at=_now_iso(),
        )

    def _default_forms(self) -> dict[str, list[AnalyticsSourceForm]]:
        day1_entry_id = _extract_survey_id(DAY1_ENTRY_FORM_URL)
        day1_exit_id = _extract_survey_id(DAY1_EXIT_FORM_URL)
        day2_id = _extract_survey_id(DAY2_FORM_URL)
        if not day1_entry_id or not day1_exit_id or not day2_id:
            raise RuntimeError("Не удалось извлечь surveyId для форм аналитики.")
        return {
            "day1": [
                AnalyticsSourceForm(id="day1-entry", name="Входная анкета первого дня", url=DAY1_ENTRY_FORM_URL, survey_id=day1_entry_id),
                AnalyticsSourceForm(id="day1-exit", name="Выходная анкета первого дня", url=DAY1_EXIT_FORM_URL, survey_id=day1_exit_id),
            ],
            "day2": [
                AnalyticsSourceForm(id="day2-main", name="Анкета второго дня", url=DAY2_FORM_URL, survey_id=day2_id),
            ],
            "summary": [],
            "infographic": [],
        }

    def _default_config(self) -> AnalyticsAgentConfig:
        forms = self._default_forms()
        return AnalyticsAgentConfig(
            blocks=[
                AnalyticsBlockConfig(id="day1", name="Первый день", description="Объединяет входную и выходную анкеты первого дня.", mode="single-date", system_prompt=DEFAULT_DAY1_PROMPT, source_forms=forms["day1"]),
                AnalyticsBlockConfig(id="day2", name="Второй день", description="Готовит записку по анкете второго дня по одной дате или диапазону дат.", mode="date-range", system_prompt=DEFAULT_DAY2_PROMPT, source_forms=forms["day2"]),
                AnalyticsBlockConfig(id="summary", name="Общая итоговая аналитика", description="Сводит готовые записки первого и второго дня.", mode="reports-only", system_prompt=DEFAULT_SUMMARY_PROMPT, source_forms=[]),
                AnalyticsBlockConfig(id="infographic", name="Инфографика", description="Создает отдельный блокнот NotebookLM и запускает инфографику по Google Doc, итоговой аналитике и визуальным ориентирам.", mode="notebooklm-infographic", system_prompt=DEFAULT_INFOGRAPHIC_PROMPT, source_forms=[]),
            ],
            synced_to_n8n=False,
            sync_message="Конфиг создан локально. Синхронизация с n8n выполнится при сохранении.",
            updated_at=_now_iso(),
        )

    def _write_config(self, config: AnalyticsAgentConfig) -> None:
        self._config_path().write_text(
            json.dumps(config.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _block_map(self, config: AnalyticsAgentConfig) -> dict[str, AnalyticsBlockConfig]:
        return {block.id: block for block in config.blocks}

    def load_config(self) -> AnalyticsAgentConfig:
        self._ensure_dirs()
        target = self._config_path()
        if not target.is_file():
            config = self._default_config()
            self._write_config(config)
            return config
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"Не удалось прочитать конфиг аналитики: {exc}") from exc
        if "blocks" not in raw:
            return self._migrate_legacy_config(raw)
        try:
            config = AnalyticsAgentConfig(**raw)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=f"Конфиг аналитики поврежден: {exc}") from exc

        return self._upgrade_config(config)

    def _migrate_legacy_config(self, raw: dict[str, Any]) -> AnalyticsAgentConfig:
        config = self._default_config()
        day2_block = next(block for block in config.blocks if block.id == "day2")
        day2_form_url = raw.get("source_form_url") or DAY2_FORM_URL
        day2_survey_id = _extract_survey_id(day2_form_url) or day2_block.source_forms[0].survey_id
        migrated = AnalyticsAgentConfig(
            blocks=[
                block if block.id != "day2" else AnalyticsBlockConfig(
                    id="day2",
                    name=day2_block.name,
                    description=day2_block.description,
                    mode=day2_block.mode,
                    system_prompt=(raw.get("system_prompt") or DEFAULT_DAY2_PROMPT).strip(),
                    source_forms=[AnalyticsSourceForm(id="day2-main", name="Анкета второго дня", url=day2_form_url, survey_id=day2_survey_id)],
                )
                for block in config.blocks
            ],
            synced_to_n8n=bool(raw.get("synced_to_n8n")),
            sync_message=raw.get("sync_message") or "Конфиг обновлен до трехблочной схемы.",
            updated_at=raw.get("updated_at") or _now_iso(),
        )
        self._write_config(migrated)
        return migrated

    def _upgrade_config(self, config: AnalyticsAgentConfig) -> AnalyticsAgentConfig:
        changed = False
        upgraded_blocks: list[AnalyticsBlockConfig] = []
        for block in config.blocks:
            if block.id != "day2":
                upgraded_blocks.append(block)
                continue
            next_description = "Готовит записку по анкете второго дня по одной дате или диапазону дат."
            if block.mode != "date-range" or block.description != next_description:
                changed = True
                upgraded_blocks.append(
                    AnalyticsBlockConfig(
                        id=block.id,
                        name=block.name,
                        description=next_description,
                        mode="date-range",
                        system_prompt=block.system_prompt,
                        source_forms=block.source_forms,
                    )
                )
                continue
            upgraded_blocks.append(block)
        if not any(block.id == "infographic" for block in upgraded_blocks):
            changed = True
            upgraded_blocks.append(
                AnalyticsBlockConfig(
                    id="infographic",
                    name="Инфографика",
                    description="Создает отдельный блокнот NotebookLM и запускает инфографику по Google Doc, итоговой аналитике и визуальным ориентирам.",
                    mode="notebooklm-infographic",
                    system_prompt=DEFAULT_INFOGRAPHIC_PROMPT,
                    source_forms=[],
                )
            )
        if not changed:
            return config
        upgraded = AnalyticsAgentConfig(
            agent_id=config.agent_id,
            name=config.name,
            description=config.description,
            blocks=upgraded_blocks,
            synced_to_n8n=config.synced_to_n8n,
            sync_message=config.sync_message,
            updated_at=_now_iso(),
        )
        self._write_config(upgraded)
        return upgraded

    async def update_config(self, request: UpdateAnalyticsAgentConfigRequest) -> AnalyticsAgentConfig:
        current = self.load_config()
        blocks = self._block_map(current)
        synced_to_n8n = False
        sync_message = "Промпты сохранены."
        if self.settings.n8n_base_url and self.settings.n8n_api_key and self.settings.n8n_fetch_workflow_id:
            try:
                await self._sync_fetch_workflow(blocks["day1"], blocks["day2"])
                synced_to_n8n = True
                sync_message = "Промпты сохранены, workflow n8n обновлен."
            except Exception as exc:  # noqa: BLE001
                sync_message = f"Промпты сохранены, но n8n не обновился: {exc}"
        updated = AnalyticsAgentConfig(
            blocks=[
                AnalyticsBlockConfig(id="day1", name=blocks["day1"].name, description=blocks["day1"].description, mode=blocks["day1"].mode, system_prompt=request.day1_prompt.strip(), source_forms=blocks["day1"].source_forms),
                AnalyticsBlockConfig(id="day2", name=blocks["day2"].name, description=blocks["day2"].description, mode=blocks["day2"].mode, system_prompt=request.day2_prompt.strip(), source_forms=blocks["day2"].source_forms),
                AnalyticsBlockConfig(id="summary", name=blocks["summary"].name, description=blocks["summary"].description, mode=blocks["summary"].mode, system_prompt=request.summary_prompt.strip(), source_forms=[]),
                AnalyticsBlockConfig(id="infographic", name=blocks["infographic"].name, description=blocks["infographic"].description, mode=blocks["infographic"].mode, system_prompt=request.infographic_prompt.strip(), source_forms=[]),
            ],
            synced_to_n8n=synced_to_n8n,
            sync_message=sync_message,
            updated_at=_now_iso(),
        )
        self._write_config(updated)
        return updated

    async def day1_history(self) -> AnalyticsDay1HistoryResponse:
        config = self.load_config()
        block = self._block_map(config)["day1"]
        bundle = await self._fetch_block_payload("day1", block)
        forms = self._normalize_block_forms(bundle["payload"], block)
        latest = self._read_latest_report("day1")
        selected_date = str(latest.get("selectedDate") or "") if latest else ""
        entry_counts = self._count_by_date(forms[0]["items"])
        exit_counts = self._count_by_date(forms[1]["items"])
        days = sorted(set(entry_counts) | set(exit_counts), reverse=True)
        options = [
            AnalyticsDateOption(
                date=day.isoformat(),
                label=_format_russian_date(day),
                count=entry_counts.get(day, 0),
                secondary_count=exit_counts.get(day, 0),
                total_count=entry_counts.get(day, 0) + exit_counts.get(day, 0),
            )
            for day in days
        ]
        return AnalyticsDay1HistoryResponse(
            total_entry_answers=len(forms[0]["items"]),
            total_exit_answers=len(forms[1]["items"]),
            available_dates=options,
            default_date=selected_date or (options[0].date if options else None),
            selected_date=selected_date or None,
            locked=bool(selected_date),
            payload_source=bundle["source"],
            payload_message=bundle["message"],
            source_forms=block.source_forms,
            timeline=self._timeline_from_state("day1", latest),
        )

    async def day2_history(self) -> AnalyticsDay2HistoryResponse:
        config = self.load_config()
        block = self._block_map(config)["day2"]
        bundle = await self._fetch_block_payload("day2", block)
        form = self._normalize_block_forms(bundle["payload"], block)[0]
        latest = self._read_latest_report("day2")
        selected_date_from = str(latest.get("dateFrom") or "") if latest else ""
        selected_date_to = str(latest.get("dateTo") or "") if latest else ""
        counts = self._count_by_date(form["items"])
        days = sorted(counts.keys(), reverse=True)
        options = [AnalyticsDateOption(date=day.isoformat(), label=_format_russian_date(day), count=counts[day], total_count=counts[day]) for day in days]
        default_date_from = selected_date_from or (options[0].date if options else None)
        default_date_to = selected_date_to or next(
            (option.date for option in options if default_date_from and option.date > default_date_from),
            None,
        )
        return AnalyticsDay2HistoryResponse(
            total_answers=len(form["items"]),
            available_dates=options,
            date_from_default=default_date_from,
            date_to_default=default_date_to,
            selected_date_from=selected_date_from or None,
            selected_date_to=selected_date_to or None,
            locked=bool(selected_date_from),
            payload_source=bundle["source"],
            payload_message=bundle["message"],
            source_forms=block.source_forms,
            timeline=self._timeline_from_state("day2", latest),
        )

    async def summary_state(self) -> AnalyticsSummaryStateResponse:
        config = self.load_config()
        blocks = self._block_map(config)
        day1 = self._report_state("day1", blocks["day1"].name)
        day2 = self._report_state("day2", blocks["day2"].name)
        dependencies_ready = day1.ready and day2.ready
        summary_latest = self._read_latest_report("summary")
        summary_is_current = bool(
            dependencies_ready
            and summary_latest
            and summary_latest.get("day1ReportCreatedAt") == day1.created_at
            and summary_latest.get("day2ReportCreatedAt") == day2.created_at
        )
        summary = self._report_state(
            "summary",
            blocks["summary"].name,
            latest=summary_latest,
            ready=summary_is_current,
            stale=bool(summary_latest) and not summary_is_current,
        )
        infographic = self._infographic_state(
            blocks["infographic"].name,
            summary_state=summary,
        )
        return AnalyticsSummaryStateResponse(
            ready=summary.ready,
            dependencies_ready=dependencies_ready,
            infographic_dependencies_ready=summary.ready,
            day1=day1,
            day2=day2,
            summary=summary,
            infographic=infographic,
        )

    async def source_status(self) -> AnalyticsSourceStatusResponse:
        config = self.load_config()
        statuses: list[AnalyticsSourceStatusItem] = []
        ok = True
        for block_id in ("day1", "day2"):
            block = self._block_map(config)[block_id]
            try:
                bundle = await self._fetch_block_payload(block_id, block)
                forms = self._normalize_block_forms(bundle["payload"], block)
                statuses.append(AnalyticsSourceStatusItem(block_id=block_id, block_name=block.name, ok=True, payload_source=bundle["source"], total_answers=sum(len(form["items"]) for form in forms), message=bundle["message"]))
            except HTTPException as exc:
                ok = False
                statuses.append(AnalyticsSourceStatusItem(block_id=block_id, block_name=block.name, ok=False, total_answers=0, message=str(exc.detail)))
        return AnalyticsSourceStatusResponse(ok=ok, statuses=statuses)

    async def _fetch_block_payload(self, block_id: str, block: AnalyticsBlockConfig) -> dict[str, Any]:
        self._ensure_dirs()
        url = (self.settings.n8n_latest_json_url or "").strip()
        errors: list[str] = []
        if url:
            try:
                async with httpx.AsyncClient(timeout=90.0, verify=self.settings.gemini_verify_ssl) as client:
                    response = await client.get(url, params={"block": block_id}, headers={"Accept": "application/json"})
                if response.status_code >= 400:
                    errors.append(f"n8n webhook вернул ошибку {response.status_code}.")
                else:
                    raw_payload = response.json()
                    if not isinstance(raw_payload, dict):
                        raise ValueError("Ответ n8n не является JSON-объектом.")
                    payload = self._coerce_payload_shape(block_id, raw_payload, block)
                    self._cache_path(block_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    return {"payload": payload, "source": "remote", "message": f"Получены свежие ответы из n8n для блока «{block.name}»."}
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                errors.append(str(exc))
        else:
            errors.append("URL вебхука n8n не настроен.")
        cache_path = self._cache_path(block_id)
        if cache_path.is_file():
            try:
                raw_cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if not isinstance(raw_cached, dict):
                    raise ValueError("Локальный кеш n8n поврежден.")
                cached = self._coerce_payload_shape(block_id, raw_cached, block)
                return {"payload": cached, "source": "cache", "message": f"{errors[0] if errors else 'Свежие данные недоступны.'} Использован локальный кеш последней выгрузки."}
            except (json.JSONDecodeError, OSError, ValueError):
                pass
        raise HTTPException(status_code=502, detail=errors[0] if errors else "Не удалось получить ответы из n8n.")

    def _coerce_payload_shape(self, block_id: str, payload: dict[str, Any], block: AnalyticsBlockConfig) -> dict[str, Any]:
        forms = payload.get("forms")
        if isinstance(forms, list):
            return {"block": payload.get("block") or block_id, "blockName": payload.get("blockName") or block.name, "forms": forms}
        if block_id == "day2" and isinstance(payload.get("items"), list):
            source_form = block.source_forms[0]
            return {"block": "day2", "blockName": block.name, "forms": [{"formId": source_form.id, "formName": source_form.name, "formUrl": source_form.url, "surveyId": payload.get("surveyId") or source_form.survey_id, "items": payload.get("items") or [], "totalAnswers": payload.get("totalAnswers")}]}
        raise ValueError(f"Структура блока {block_id} из n8n не соответствует ожидаемому формату.")

    def _normalize_block_forms(self, payload: dict[str, Any], block: AnalyticsBlockConfig) -> list[dict[str, Any]]:
        forms_by_id = {form["formId"]: form for form in (self._normalize_form_payload(raw_form) for raw_form in payload.get("forms") or [] if isinstance(raw_form, dict))}
        normalized: list[dict[str, Any]] = []
        for source_form in block.source_forms:
            normalized.append(forms_by_id.get(source_form.id) or {"formId": source_form.id, "formName": source_form.name, "formUrl": source_form.url, "surveyId": source_form.survey_id, "items": []})
        return normalized

    def _normalize_form_payload(self, raw_form: dict[str, Any]) -> dict[str, Any]:
        items = raw_form.get("items") if isinstance(raw_form.get("items"), list) else []
        responses: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("created"):
                continue
            answers = [{"order": answer.get("order"), "question": _clean_string(str(answer.get("question", ""))), "slug": answer.get("slug"), "type": answer.get("type"), "rows": [_clean_string(str(row)) for row in (answer.get("rows") or [])], "value": _clean_value(answer.get("value"))} for answer in (item.get("answers") or []) if isinstance(answer, dict)]
            responses.append({"surveyId": raw_form.get("surveyId") or item.get("surveyId"), "answerId": item.get("answerId"), "created": item["created"], "answers": answers})
        return {"formId": raw_form.get("formId"), "formName": raw_form.get("formName"), "formUrl": raw_form.get("formUrl"), "surveyId": raw_form.get("surveyId"), "items": responses}

    def _response_local_date(self, item: dict[str, Any]) -> date:
        return _parse_created(str(item["created"])).astimezone(self.settings.timezone()).date()

    def _count_by_date(self, items: list[dict[str, Any]]) -> dict[date, int]:
        counts: dict[date, int] = {}
        for item in items:
            day = self._response_local_date(item)
            counts[day] = counts.get(day, 0) + 1
        return counts

    def _filter_items_by_day(self, items: list[dict[str, Any]], selected_date: date) -> list[dict[str, Any]]:
        return [item for item in items if self._response_local_date(item) == selected_date]

    def _filter_items_by_range(self, items: list[dict[str, Any]], date_from: date, date_to: date | None) -> list[dict[str, Any]]:
        if date_to is None:
            return self._filter_items_by_day(items, date_from)
        return [item for item in items if date_from <= self._response_local_date(item) <= date_to]

    def _sanitize_report_text(self, report_text: str) -> str:
        lines = report_text.replace("\r", "").split("\n")
        banned_fragments = (
            "myactivity.google.com/product/gemini",
            "историю действий в приложениях gemini",
            "совпадений в json",
            "автоматического сопоставления id участников",
        )
        banned_prefixes = (
            "дата анализа:",
            "входных анкет:",
            "выходных анкет:",
            "источники:",
        )

        cleaned_lines: list[str] = []
        for raw_line in lines:
            stripped = raw_line.strip()
            lowered = stripped.lower()
            if stripped and any(fragment in lowered for fragment in banned_fragments):
                continue
            if any(lowered.startswith(prefix) for prefix in banned_prefixes):
                continue
            cleaned_lines.append(raw_line.rstrip())

        if not any(line.lstrip().startswith("# ") for line in cleaned_lines):
            for index, raw_line in enumerate(cleaned_lines):
                stripped = raw_line.strip()
                if stripped.startswith("## "):
                    cleaned_lines[index] = f"# {stripped[3:].strip()}"
                    break
                if stripped.startswith("### "):
                    cleaned_lines[index] = f"# {stripped[4:].strip()}"
                    break

        return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()

    def _answer_text_by_hint(self, item: dict[str, Any], hints: tuple[str, ...]) -> str | None:
        for answer in item.get("answers") or []:
            question = str(answer.get("question") or "").lower()
            slug = str(answer.get("slug") or "").lower()
            if any(hint in question or hint in slug for hint in hints):
                value = answer.get("value")
                if value is None:
                    return None
                return (" | ".join(str(part) for part in value if part is not None) if isinstance(value, list) else str(value)).strip() or None
        return None

    def _matched_participants(self, entry_items: list[dict[str, Any]], exit_items: list[dict[str, Any]]) -> list[str]:
        def participant_key(item: dict[str, Any]) -> str | None:
            fio = self._answer_text_by_hint(item, ("фио",))
            org = self._answer_text_by_hint(item, ("организац", "organization"))
            parts = [part for part in (fio, org) if part]
            return " | ".join(parts) if parts else None
        return sorted({key for key in (participant_key(item) for item in entry_items) if key} & {key for key in (participant_key(item) for item in exit_items) if key})

    def _build_form_payload(self, form: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
        question_count = max((len(item.get("answers") or []) for item in items), default=0)
        questions: list[dict[str, Any]] = []
        for index in range(question_count):
            template = next(
                (
                    answers[index]
                    for answers in (item.get("answers") or [] for item in items)
                    if index < len(answers) and isinstance(answers[index], dict)
                ),
                None,
            )
            if template is None:
                continue
            question = {
                "key": f"q{index + 1:02d}",
                "question": template.get("question"),
            }
            if template.get("slug"):
                question["slug"] = template.get("slug")
            if template.get("type"):
                question["type"] = template.get("type")
            if template.get("rows"):
                question["rows"] = template.get("rows")
            questions.append(question)

        responses: list[dict[str, Any]] = []
        for item in items:
            values: dict[str, Any] = {}
            for index, answer in enumerate(item.get("answers") or []):
                value = _clean_value(answer.get("value"))
                if value in (None, "", [], {}):
                    continue
                values[f"q{index + 1:02d}"] = value
            responses.append(
                {
                    "answerId": item.get("answerId"),
                    "created": item.get("created"),
                    "values": values,
                }
            )
        return {
            "formId": form.get("formId"),
            "formName": form.get("formName"),
            "formUrl": form.get("formUrl"),
            "surveyId": form.get("surveyId"),
            "totalAnswers": len(items),
            "questions": questions,
            "responses": responses,
        }

    async def _generate_report_text(self, prompt: str, *, direct_model: str | None = None) -> tuple[str, str, str]:
        direct_error: str | None = None
        if self.direct_service is not None:
            try:
                timeout_sec = max(45, min(int(self.settings.gemini_timeout_sec), 120))
                async with asyncio.timeout(timeout_sec):
                    result = await self.direct_service.generate(
                        GenerateRequest(prompt=prompt, temporary=True, model=direct_model)
                    )
                text = self._sanitize_report_text((result.text or "").strip())
                if text:
                    return text, "direct", "Сформировано через прямой Gemini-клиент."
                direct_error = "Прямой Gemini-клиент не вернул текст отчета."
            except TimeoutError:
                direct_error = f"РџСЂСЏРјРѕР№ Gemini-РєР»РёРµРЅС‚ РЅРµ РѕС‚РІРµС‚РёР» Р·Р° {timeout_sec} СЃРµРє."
            except Exception as exc:  # noqa: BLE001
                direct_error = str(exc)
        try:
            run = await self.web_runner.run(WebGenerateRequest(prompt=prompt, mode="pro", headless=True, timeout_sec=180, wait_after_submit_sec=25))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Не удалось сформировать аналитическую записку. Direct: {direct_error or 'неизвестно'}. Web: {exc}") from exc
        text = self._sanitize_report_text((run.assistant_text or run.last_turn_text or "").strip())
        if not text:
            raise HTTPException(status_code=502, detail="Gemini web runner завершился без текста отчета.")
        return text, "web", "Сформировано через резервный Gemini web runner."

    def _save_report(self, block_id: str, suffix: str, report_text: str, prompt: str, payload_name: str, payload: dict[str, Any], docx_name: str, meta_lines: list[tuple[str, str]], source_forms: list[AnalyticsSourceForm]) -> tuple[Path, str, Path, str]:
        stamp = datetime.now(self.settings.timezone()).strftime("%Y%m%d_%H%M%S")
        run_dir = self._reports_root(block_id) / f"{stamp}_{_file_safe_slug(suffix)}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / payload_name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        (run_dir / "report.md").write_text(report_text, encoding="utf-8")
        title = self._extract_report_title(report_text)
        docx_path = self._render_docx(report_text, run_dir / docx_name, title, meta_lines, source_forms)
        relative = docx_path.relative_to(self.settings.downloads_root()).as_posix()
        return run_dir, title, docx_path, relative

    def _extract_report_title(self, report_text: str) -> str:
        for line in report_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return "Аналитическая записка"

    def _write_latest_report(self, block_id: str, payload: dict[str, Any]) -> None:
        self._latest_report_path(block_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_latest_report(self, block_id: str) -> dict[str, Any] | None:
        target = self._latest_report_path(block_id)
        if not target.is_file():
            return None
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else None
        except (json.JSONDecodeError, OSError):
            return None

    def _read_report_text(self, meta: dict[str, Any]) -> str:
        if isinstance(meta.get("reportText"), str) and str(meta["reportText"]).strip():
            return str(meta["reportText"])
        report_path = Path(str(meta.get("reportPath") or ""))
        if not report_path.is_file():
            raise HTTPException(status_code=500, detail=f"Не найден файл записки: {report_path}")
        return report_path.read_text(encoding="utf-8")

    def _report_state(
        self,
        block_id: str,
        block_name: str,
        *,
        latest: dict[str, Any] | None = None,
        ready: bool | None = None,
        stale: bool = False,
    ) -> AnalyticsReportState:
        payload = latest if latest is not None else self._read_latest_report(block_id)
        timeline_summary = None
        if stale:
            timeline_summary = "Исходные данные обновились. Итог нужно пересобрать."
        elif payload is not None:
            timeline_summary = "Последний запуск завершен успешно."
        if payload is None:
            return AnalyticsReportState(
                block_id=block_id,
                block_name=block_name,
                ready=False,
                timeline=self._timeline_from_state(block_id, None, summary=timeline_summary),
            )  # type: ignore[arg-type]
        return AnalyticsReportState(
            block_id=block_id,
            block_name=block_name,
            ready=True if ready is None else ready,
            stale=stale,
            title=payload.get("title"),
            created_at=payload.get("createdAt"),
            period_label=payload.get("periodLabel"),
            document_name=payload.get("documentName"),
            document_url=payload.get("documentUrl"),
            selected_date=payload.get("selectedDate"),
            date_from=payload.get("dateFrom"),
            date_to=payload.get("dateTo"),
            timeline=self._timeline_from_state(block_id, payload, summary=timeline_summary),
        )  # type: ignore[arg-type]

    def _infographic_state(
        self,
        block_name: str,
        *,
        summary_state: AnalyticsReportState,
    ) -> AnalyticsInfographicState:
        latest = self._read_latest_report("infographic")
        ready = bool(
            summary_state.ready
            and latest
            and latest.get("summaryReportCreatedAt") == summary_state.created_at
            and latest.get("imageUrl")
        )
        stale = bool(latest) and not ready
        timeline_summary = None
        if stale:
            timeline_summary = "Итоговая аналитика изменилась. Инфографику нужно пересобрать."
        elif latest is not None:
            timeline_summary = "Последний запуск завершен успешно."
        if latest is None:
            return AnalyticsInfographicState(
                block_name=block_name,
                ready=False,
                timeline=self._timeline_from_state("infographic", None, summary=timeline_summary),
            )

        return AnalyticsInfographicState(
            block_name=block_name,
            ready=ready,
            stale=stale,
            created_at=latest.get("createdAt"),
            notebook_id=latest.get("notebookId"),
            notebook_title=latest.get("notebookTitle"),
            notebook_url=latest.get("notebookUrl"),
            profile=latest.get("profile"),
            google_doc_url=latest.get("googleDocUrl"),
            summary_created_at=latest.get("summaryReportCreatedAt"),
            summary_title=latest.get("summaryTitle"),
            photo_name=latest.get("photoName"),
            photo_url=latest.get("photoUrl"),
            logo_name=latest.get("logoName"),
            logo_url=latest.get("logoUrl"),
            image_name=latest.get("imageName"),
            image_url=latest.get("imageUrl"),
            studio_items=list(latest.get("studioItems") or []),
            timeline=self._timeline_from_state("infographic", latest, summary=timeline_summary),
        )

    async def _send_report_to_n8n(self, *, block_id: str, block_name: str, report_text: str, docx_path: Path, title: str, period_label: str, source_form_urls: list[str]) -> tuple[bool, str]:
        url = (self.settings.n8n_report_ingest_url or "").strip()
        if not url:
            return False, "Webhook обратной передачи в n8n пока не настроен."
        payload = {"blockId": block_id, "blockName": block_name, "title": title, "periodLabel": period_label, "sourceFormUrls": source_form_urls, "reportText": report_text, "documentName": docx_path.name, "documentMimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "documentBase64": base64.b64encode(docx_path.read_bytes()).decode("ascii"), "sentAt": _now_iso()}
        try:
            async with httpx.AsyncClient(timeout=90.0, verify=self.settings.gemini_verify_ssl) as client:
                response = await client.post(url, json=payload, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            return False, f"Не удалось передать отчет в n8n: {exc}"
        return (False, f"n8n intake вернул ошибку {response.status_code}.") if response.status_code >= 400 else (True, "Отчет отправлен в n8n для дальнейшей обработки.")

    async def _sync_fetch_workflow(self, day1_block: AnalyticsBlockConfig, day2_block: AnalyticsBlockConfig) -> None:
        base_url = (self.settings.n8n_base_url or "").rstrip("/")
        api_key = (self.settings.n8n_api_key or "").strip()
        workflow_id = (self.settings.n8n_fetch_workflow_id or "").strip()
        if not base_url or not api_key or not workflow_id:
            raise RuntimeError("Не настроен доступ к n8n API для синхронизации workflow.")
        headers = {"Accept": "application/json", "Content-Type": "application/json", "X-N8N-API-KEY": api_key}
        async with httpx.AsyncClient(timeout=90.0, verify=self.settings.gemini_verify_ssl) as client:
            response = await client.get(f"{base_url}/api/v1/workflows/{workflow_id}", headers=headers)
            response.raise_for_status()
            payload = build_fetch_workflow_payload(existing_workflow=response.json(), day1_entry_survey_id=day1_block.source_forms[0].survey_id, day1_exit_survey_id=day1_block.source_forms[1].survey_id, day2_survey_id=day2_block.source_forms[0].survey_id, day1_entry_form_url=day1_block.source_forms[0].url, day1_exit_form_url=day1_block.source_forms[1].url, day2_form_url=day2_block.source_forms[0].url)
            update = await client.put(f"{base_url}/api/v1/workflows/{workflow_id}", headers=headers, json=payload)
            update.raise_for_status()

    def _render_docx(self, report_text: str, output_path: Path, title: str, meta_lines: list[tuple[str, str]], source_forms: list[AnalyticsSourceForm]) -> Path:
        return render_markdown_to_docx(
            report_text=report_text,
            output_path=output_path,
            title=title,
            meta_lines=meta_lines,
            source_notes=[f"{form.name} — {form.url}" for form in source_forms],
        )

    def reset_state(self) -> AnalyticsResetResponse:
        cleared_blocks = [block_id for block_id in ("day1", "day2", "summary", "infographic") if self._clear_latest_report(block_id)]
        for block_id in ("day1", "day2", "summary", "infographic"):
            self._timeline_tracker.clear(block_id)
        return AnalyticsResetResponse(
            ok=True,
            cleared_blocks=cleared_blocks,
            message="Текущие состояния аналитики сброшены. Можно заново выбрать даты и собрать документы.",
        )

    async def run_day1(self, request: AnalyticsDay1RunRequest) -> AnalyticsDay1RunResponse:
        async with self._run_lock:
            latest = self._read_latest_report("day1")
            locked_date = str(latest.get("selectedDate") or "") if latest else ""
            if locked_date and request.date is not None and locked_date != request.date.isoformat():
                raise HTTPException(status_code=409, detail=f"Дата первого дня уже зафиксирована: {locked_date}. Сначала выполните сброс.")
            if request.date is None:
                raise HTTPException(status_code=400, detail="Выберите дату первого дня.")

            config = self.load_config()
            block = self._block_map(config)["day1"]
            current_step = "fetch"
            timeline_started = False

            try:
                self._timeline_tracker.start(
                    "day1",
                    self._timeline_steps("day1"),
                    summary="Собираю аналитику первого дня.",
                    first_message="Получаю входную и выходную анкеты из источника.",
                )
                timeline_started = True
                bundle = await self._fetch_block_payload("day1", block)

                current_step = "prepare"
                self._timeline_tracker.advance("day1", current_step, message="Фиксирую дату и готовлю выборку ответов.")
                forms = self._normalize_block_forms(bundle["payload"], block)
                entry_filtered = self._filter_items_by_day(forms[0]["items"], request.date)
                exit_filtered = self._filter_items_by_day(forms[1]["items"], request.date)
                if not entry_filtered and not exit_filtered:
                    raise HTTPException(status_code=400, detail="За выбранную дату анкеты первого дня не найдены.")

                payload = {
                    "blockId": "day1",
                    "selectedDate": request.date.isoformat(),
                    "selectedDateLabel": _format_russian_date(request.date),
                    "summary": {
                        "entryAnswers": len(entry_filtered),
                        "exitAnswers": len(exit_filtered),
                    },
                    "forms": [self._build_form_payload(forms[0], entry_filtered), self._build_form_payload(forms[1], exit_filtered)],
                }

                current_step = "generate"
                self._timeline_tracker.advance("day1", current_step, message="Готовлю prompt и отправляю сводные данные в модель.")
                prompt = (
                    f"{block.system_prompt.strip()}\n\n"
                    "Контекст запуска:\n"
                    f"- Дата первого дня: {payload['selectedDateLabel']}\n"
                    f"- Входных анкет: {payload['summary']['entryAnswers']}\n"
                    f"- Выходных анкет: {payload['summary']['exitAnswers']}\n\n"
                    f"JSON с анкетами первого дня:\n{_compact_json(payload)}"
                )
                report_text, generation_method, generation_message = await self._generate_report_text(prompt)

                current_step = "convert"
                self._timeline_tracker.advance("day1", current_step, message="Собираю DOCX из Markdown-отчета.")
                created_at = _now_iso()
                period_label = payload["selectedDateLabel"]
                run_dir, title, docx_path, relative = self._save_report(
                    "day1",
                    request.date.isoformat(),
                    report_text,
                    prompt,
                    "day1_payload.json",
                    payload,
                    "analytics-day1.docx",
                    [],
                    [],
                )

                current_step = "sync"
                self._timeline_tracker.advance("day1", current_step, message="Передаю собранный документ и текст в n8n.")
                roundtrip_ok, roundtrip_message = await self._send_report_to_n8n(
                    block_id="day1",
                    block_name=block.name,
                    report_text=report_text,
                    docx_path=docx_path,
                    title=title,
                    period_label=period_label,
                    source_form_urls=[form.url for form in block.source_forms],
                )
                self._timeline_tracker.touch(
                    "day1",
                    current_step,
                    message=roundtrip_message or ("Результат передан в n8n." if roundtrip_ok else "Документ готов локально."),
                )

                done_message = "Документ первого дня готов."
                if roundtrip_message and not roundtrip_ok:
                    done_message = f"{done_message} {roundtrip_message}"
                timeline = self._timeline_tracker.finish("day1", "done", message=done_message, summary=done_message)
                self._write_latest_report(
                    "day1",
                    {
                        "blockId": "day1",
                        "blockName": block.name,
                        "createdAt": created_at,
                        "title": title,
                        "periodLabel": period_label,
                        "selectedDate": request.date.isoformat(),
                        "reportPath": str(run_dir / "report.md"),
                        "reportText": report_text,
                        "documentName": docx_path.name,
                        "documentPath": str(docx_path),
                        "documentUrl": f"/downloads/{relative}",
                        "timeline": timeline.model_dump(),
                    },
                )
                self._clear_latest_report("summary")
                self._clear_latest_report("infographic")
                self._timeline_tracker.clear("summary")
                self._timeline_tracker.clear("infographic")
                self._timeline_tracker.clear("day1")

                return AnalyticsDay1RunResponse(
                    ok=True,
                    block_name=block.name,
                    selected_date=request.date.isoformat(),
                    entry_answers=len(entry_filtered),
                    exit_answers=len(exit_filtered),
                    payload_source=bundle["source"],
                    payload_message=bundle["message"],
                    generation_method=generation_method,
                    generation_message=generation_message,
                    report_text=report_text,
                    document_name=docx_path.name,
                    document_path=str(docx_path),
                    document_url=f"/downloads/{relative}",
                    n8n_roundtrip_ok=roundtrip_ok,
                    n8n_roundtrip_message=roundtrip_message,
                    created_at=created_at,
                    title=title,
                    timeline=timeline,
                )
            except HTTPException as exc:
                if timeline_started:
                    self._timeline_tracker.fail("day1", current_step, message=str(exc.detail))
                raise
            except Exception as exc:
                if timeline_started:
                    self._timeline_tracker.fail("day1", current_step, message=str(exc))
                raise

    async def run_day2(self, request: AnalyticsDay2RunRequest) -> AnalyticsDay2RunResponse:
        async with self._run_lock:
            latest = self._read_latest_report("day2")
            locked_date_from = str(latest.get("dateFrom") or "") if latest else ""
            locked_date_to = str(latest.get("dateTo") or "") if latest else ""
            requested_date_to = request.date_to.isoformat() if request.date_to else ""
            if locked_date_from and request.date_from is not None and (
                locked_date_from != request.date_from.isoformat() or locked_date_to != requested_date_to
            ):
                locked_period = locked_date_from if not locked_date_to else f"{locked_date_from} - {locked_date_to}"
                raise HTTPException(status_code=409, detail=f"Период второго дня уже зафиксирован: {locked_period}. Сначала выполните сброс.")
            if request.date_from is None:
                raise HTTPException(status_code=400, detail="Выберите первую дату для второго дня.")
            if request.date_to is not None and request.date_to <= request.date_from:
                raise HTTPException(status_code=400, detail="Вторая дата должна быть позже первой. Если нужен один день, оставьте вторую дату пустой.")

            config = self.load_config()
            block = self._block_map(config)["day2"]
            current_step = "fetch"
            timeline_started = False

            try:
                self._timeline_tracker.start(
                    "day2",
                    self._timeline_steps("day2"),
                    summary="Собираю аналитику второго дня.",
                    first_message="Получаю ответы второй анкеты из источника.",
                )
                timeline_started = True
                bundle = await self._fetch_block_payload("day2", block)

                current_step = "prepare"
                self._timeline_tracker.advance("day2", current_step, message="Фиксирую дату и готовлю выборку ответов.")
                form = self._normalize_block_forms(bundle["payload"], block)[0]
                effective_date_to = request.date_to if request.date_to else None
                filtered = self._filter_items_by_range(form["items"], request.date_from, effective_date_to)
                if not filtered:
                    raise HTTPException(status_code=400, detail="За выбранный период анкеты второго дня не найдены.")

                filter_mode = "range" if effective_date_to else "single-day"
                period_label = f"{_format_russian_date(request.date_from)} - {_format_russian_date(effective_date_to)}" if effective_date_to else _format_russian_date(request.date_from)
                payload = {
                    "blockId": "day2",
                    "filterMode": filter_mode,
                    "dateFrom": request.date_from.isoformat(),
                    "dateTo": effective_date_to.isoformat() if effective_date_to else None,
                    "periodLabel": period_label,
                    "forms": [self._build_form_payload(form, filtered)],
                }

                current_step = "generate"
                self._timeline_tracker.advance("day2", current_step, message="Готовлю prompt и отправляю ответы второго дня в модель.")
                prompt = (
                    f"{block.system_prompt.strip()}\n\n"
                    "Контекст запуска:\n"
                    f"- Период: {period_label}\n"
                    f"- Количество ответов: {len(filtered)}\n\n"
                    f"JSON с анкетой второго дня:\n{_compact_json(payload)}"
                )
                report_text, generation_method, generation_message = await self._generate_report_text(prompt)

                current_step = "convert"
                self._timeline_tracker.advance("day2", current_step, message="Собираю DOCX из Markdown-отчета.")
                created_at = _now_iso()
                run_dir, title, docx_path, relative = self._save_report(
                    "day2",
                    request.date_from.isoformat() if not effective_date_to else f"{request.date_from.isoformat()}_{effective_date_to.isoformat()}",
                    report_text,
                    prompt,
                    "day2_payload.json",
                    payload,
                    "analytics-day2.docx",
                    [
                        ("Дата генерации", datetime.now(self.settings.timezone()).strftime("%Y-%m-%d %H:%M")),
                        ("Период анализа", period_label),
                        ("Количество ответов", str(len(filtered))),
                    ],
                    block.source_forms,
                )

                current_step = "sync"
                self._timeline_tracker.advance("day2", current_step, message="Передаю собранный документ и текст в n8n.")
                roundtrip_ok, roundtrip_message = await self._send_report_to_n8n(
                    block_id="day2",
                    block_name=block.name,
                    report_text=report_text,
                    docx_path=docx_path,
                    title=title,
                    period_label=period_label,
                    source_form_urls=[form.url for form in block.source_forms],
                )
                self._timeline_tracker.touch(
                    "day2",
                    current_step,
                    message=roundtrip_message or ("Результат передан в n8n." if roundtrip_ok else "Документ готов локально."),
                )

                done_message = "Документ второго дня готов."
                if roundtrip_message and not roundtrip_ok:
                    done_message = f"{done_message} {roundtrip_message}"
                timeline = self._timeline_tracker.finish("day2", "done", message=done_message, summary=done_message)
                self._write_latest_report(
                    "day2",
                    {
                        "blockId": "day2",
                        "blockName": block.name,
                        "createdAt": created_at,
                        "title": title,
                        "periodLabel": period_label,
                        "dateFrom": request.date_from.isoformat(),
                        "dateTo": effective_date_to.isoformat() if effective_date_to else None,
                        "filterMode": filter_mode,
                        "reportPath": str(run_dir / "report.md"),
                        "reportText": report_text,
                        "documentName": docx_path.name,
                        "documentPath": str(docx_path),
                        "documentUrl": f"/downloads/{relative}",
                        "timeline": timeline.model_dump(),
                    },
                )
                self._clear_latest_report("summary")
                self._clear_latest_report("infographic")
                self._timeline_tracker.clear("summary")
                self._timeline_tracker.clear("infographic")
                self._timeline_tracker.clear("day2")

                return AnalyticsDay2RunResponse(
                    ok=True,
                    block_name=block.name,
                    date_from=request.date_from.isoformat(),
                    date_to=effective_date_to.isoformat() if effective_date_to else None,
                    filter_mode=filter_mode,
                    total_answers=len(form["items"]),
                    filtered_answers=len(filtered),
                    payload_source=bundle["source"],
                    payload_message=bundle["message"],
                    generation_method=generation_method,
                    generation_message=generation_message,
                    report_text=report_text,
                    document_name=docx_path.name,
                    document_path=str(docx_path),
                    document_url=f"/downloads/{relative}",
                    n8n_roundtrip_ok=roundtrip_ok,
                    n8n_roundtrip_message=roundtrip_message,
                    created_at=created_at,
                    title=title,
                    timeline=timeline,
                )
            except HTTPException as exc:
                if timeline_started:
                    self._timeline_tracker.fail("day2", current_step, message=str(exc.detail))
                raise
            except Exception as exc:
                if timeline_started:
                    self._timeline_tracker.fail("day2", current_step, message=str(exc))
                raise

    async def run_summary(self, _: AnalyticsSummaryRunRequest) -> AnalyticsSummaryRunResponse:
        async with self._run_lock:
            config = self.load_config()
            block = self._block_map(config)["summary"]
            current_step = "prepare"
            timeline_started = False

            try:
                self._timeline_tracker.start(
                    "summary",
                    self._timeline_steps("summary"),
                    summary="Собираю итоговую аналитику.",
                    first_message="Проверяю готовность текстов первого и второго дня.",
                )
                timeline_started = True
                day1 = self._read_latest_report("day1")
                day2 = self._read_latest_report("day2")
                if day1 is None or day2 is None:
                    raise HTTPException(status_code=400, detail="Сначала сформируйте аналитические записки первого и второго дня.")

                day1_text = self._read_report_text(day1)
                day2_text = self._read_report_text(day2)

                current_step = "generate"
                self._timeline_tracker.advance("summary", current_step, message="Синтезирую итоговую аналитику только из готовых текстов day1 и day2.")
                prompt = (
                    f"{block.system_prompt.strip()}\n\n"
                    "Контекст запуска:\n"
                    f"- Первый день: {day1.get('periodLabel') or 'готовая записка'}\n"
                    f"- Второй день: {day2.get('periodLabel') or 'готовая записка'}\n\n"
                    f"Аналитическая записка первого дня:\n{day1_text}\n\n"
                    f"Аналитическая записка второго дня:\n{day2_text}"
                )
                report_text, generation_method, generation_message = await self._generate_report_text(
                    prompt,
                    direct_model=SUMMARY_DIRECT_MODEL_NAME,
                )

                created_at = _now_iso()
                summary_period_label = " / ".join(
                    part
                    for part in (str(day1.get("periodLabel") or "").strip(), str(day2.get("periodLabel") or "").strip())
                    if part
                ) or "Первый и второй день"
                summary_payload = {
                    "blockId": "summary",
                    "builtFrom": "text-reports",
                    "day1": {
                        "createdAt": day1.get("createdAt"),
                        "periodLabel": day1.get("periodLabel"),
                        "selectedDate": day1.get("selectedDate"),
                        "reportText": day1_text,
                    },
                    "day2": {
                        "createdAt": day2.get("createdAt"),
                        "periodLabel": day2.get("periodLabel"),
                        "dateFrom": day2.get("dateFrom"),
                        "dateTo": day2.get("dateTo"),
                        "reportText": day2_text,
                    },
                }

                current_step = "convert"
                self._timeline_tracker.advance("summary", current_step, message="Собираю DOCX из итогового Markdown.")
                run_dir, title, docx_path, relative = self._save_report(
                    "summary",
                    "overall",
                    report_text,
                    prompt,
                    "summary_payload.json",
                    summary_payload,
                    "analytics-summary.docx",
                    [
                        ("Дата генерации", datetime.now(self.settings.timezone()).strftime("%Y-%m-%d %H:%M")),
                        ("Основа", "Готовые записки первого и второго дня"),
                        ("Первый день", str(day1.get("periodLabel") or "")),
                        ("Второй день", str(day2.get("periodLabel") or "")),
                    ],
                    [],
                )

                current_step = "sync"
                self._timeline_tracker.advance("summary", current_step, message="Передаю итоговый документ и текст в n8n.")
                roundtrip_ok, roundtrip_message = await self._send_report_to_n8n(
                    block_id="summary",
                    block_name=block.name,
                    report_text=report_text,
                    docx_path=docx_path,
                    title=title,
                    period_label=summary_period_label,
                    source_form_urls=[],
                )
                self._timeline_tracker.touch(
                    "summary",
                    current_step,
                    message=roundtrip_message or ("Результат передан в n8n." if roundtrip_ok else "Документ готов локально."),
                )

                done_message = "Итоговая аналитика готова."
                if roundtrip_message and not roundtrip_ok:
                    done_message = f"{done_message} {roundtrip_message}"
                timeline = self._timeline_tracker.finish("summary", "done", message=done_message, summary=done_message)
                self._write_latest_report(
                    "summary",
                    {
                        "blockId": "summary",
                        "blockName": block.name,
                        "createdAt": created_at,
                        "title": title,
                        "periodLabel": summary_period_label,
                        "day1ReportCreatedAt": day1.get("createdAt"),
                        "day2ReportCreatedAt": day2.get("createdAt"),
                        "reportPath": str(run_dir / "report.md"),
                        "reportText": report_text,
                        "documentName": docx_path.name,
                        "documentPath": str(docx_path),
                        "documentUrl": f"/downloads/{relative}",
                        "timeline": timeline.model_dump(),
                    },
                )
                self._clear_latest_report("infographic")
                self._timeline_tracker.clear("infographic")
                self._timeline_tracker.clear("summary")

                return AnalyticsSummaryRunResponse(
                    ok=True,
                    block_name=block.name,
                    generation_method=generation_method,
                    generation_message=generation_message,
                    report_text=report_text,
                    document_name=docx_path.name,
                    document_path=str(docx_path),
                    document_url=f"/downloads/{relative}",
                    n8n_roundtrip_ok=roundtrip_ok,
                    n8n_roundtrip_message=roundtrip_message,
                    created_at=created_at,
                    title=title,
                    day1_report_created_at=str(day1.get("createdAt") or ""),
                    day2_report_created_at=str(day2.get("createdAt") or ""),
                    day1_period_label=day1.get("periodLabel"),
                    day2_period_label=day2.get("periodLabel"),
                    timeline=timeline,
                )
            except HTTPException as exc:
                if timeline_started:
                    self._timeline_tracker.fail("summary", current_step, message=str(exc.detail))
                raise
            except Exception as exc:
                if timeline_started:
                    self._timeline_tracker.fail("summary", current_step, message=str(exc))
                raise

    def _require_notebooklm(self) -> NotebookLMService:
        if self.notebooklm_service is None:
            raise HTTPException(status_code=500, detail="NotebookLM service is not configured.")
        return self.notebooklm_service

    def _public_download_url(self, target: Path) -> str:
        relative = target.relative_to(self.settings.downloads_root()).as_posix()
        return f"/downloads/{relative}"

    async def _save_infographic_asset(self, upload: Any, run_dir: Path, slot: str) -> tuple[str, Path, str]:
        filename = str(getattr(upload, "filename", "") or "").strip()
        content_type = str(getattr(upload, "content_type", "") or "").strip().lower()
        if content_type and not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"Файл '{filename or slot}' должен быть изображением.")
        content = await upload.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"Файл '{filename or slot}' пустой.")
        suffix = Path(filename).suffix.lower() or ".png"
        target = run_dir / f"{slot}{suffix}"
        target.write_bytes(content)
        return filename or target.name, target, self._public_download_url(target)

    def _file_sha256(self, path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def _reuse_infographic_description(self, image_path: Path, slot: str) -> str | None:
        expected_hash = self._file_sha256(image_path)
        if not expected_hash:
            return None
        reports_root = self._reports_root("infographic")
        if not reports_root.is_dir():
            return None
        current_run_dir = image_path.parent.resolve()
        candidates = sorted(
            (item for item in reports_root.iterdir() if item.is_dir()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for run_dir in candidates:
            try:
                resolved = run_dir.resolve()
            except OSError:
                continue
            if resolved == current_run_dir:
                continue
            description_path = run_dir / f"{slot}_description.txt"
            if not description_path.is_file():
                continue
            for asset_path in run_dir.glob(f"{slot}.*"):
                if not asset_path.is_file():
                    continue
                if self._file_sha256(asset_path) != expected_hash:
                    continue
                text = description_path.read_text(encoding="utf-8").strip()
                if text:
                    return text
        return None

    def _fallback_infographic_description(self, slot: str, label: str, image_path: Path) -> str:
        if slot == "logo":
            return "\n".join(
                [
                    f"Объект: пользовательский {label} из файла {image_path.name}.",
                    "Композиция: использовать как отдельный фирменный элемент без изменения формы и пропорций.",
                    "Основные цвета: брать напрямую из исходного изображения.",
                    "Настроение: сохранить официальный и узнаваемый стиль исходного брендинга.",
                    "Важные визуальные акценты: читаемость, контраст с фоном и достаточные отступы вокруг знака.",
                ]
            )
        return "\n".join(
            [
                f"Объект: пользовательское {label} из файла {image_path.name}.",
                "Композиция: опираться на реальную композицию исходного изображения без домысливания скрытых деталей.",
                "Основные цвета: брать напрямую из исходного изображения.",
                "Настроение: сохранить визуальный характер исходного фото.",
                "Важные визуальные акценты: опираться на ключевые объекты и фактические акценты, которые видны на изображении.",
            ]
        )

    async def _describe_infographic_image(self, image_path: Path, label: str, *, slot: str) -> str:
        prompt = (
            f"Опиши {label} для подготовки инфографики. "
            "Нужны: сюжет или объект, композиция, основные цвета, настроение, важные визуальные акценты. "
            "Пиши кратко и по делу, без догадок о том, чего на изображении не видно."
        )
        if self.direct_service is not None:
            try:
                result = await self.direct_service.generate(
                    GenerateRequest(
                        prompt=prompt,
                        temporary=True,
                        files=[InputFile(path=str(image_path))],
                    )
                )
                text = str(result.text or "").strip()
                if text:
                    return re.sub(r"\n{3,}", "\n\n", text).strip()
            except Exception:
                pass

        try:
            result = await self.web_runner.run(
                WebGenerateRequest(
                    prompt=prompt,
                    file_path=str(image_path),
                    headless=True,
                    timeout_sec=120,
                    wait_after_submit_sec=20,
                )
            )
            text = str(result.assistant_text or result.last_turn_text or "").strip()
            if text:
                return re.sub(r"\n{3,}", "\n\n", text).strip()
        except Exception:
            pass

        cached = self._reuse_infographic_description(image_path, slot)
        if cached:
            return cached

        return self._fallback_infographic_description(slot, label, image_path)

    async def run_infographic(
        self,
        request: AnalyticsInfographicRunRequest,
        photo_upload: Any,
        logo_upload: Any,
    ) -> AnalyticsInfographicRunResponse:
        async with self._run_lock:
            config = self.load_config()
            block = self._block_map(config)["infographic"]
            summary_state = await self.summary_state()
            if not summary_state.summary.ready:
                raise HTTPException(status_code=400, detail="Сначала соберите актуальную итоговую аналитику.")

            summary_latest = self._read_latest_report("summary")
            if summary_latest is None:
                raise HTTPException(status_code=400, detail="Итоговая аналитика пока отсутствует.")

            notebooklm = self._require_notebooklm()
            profile = notebooklm._profile_name(None)
            current_step = "prepare"
            timeline_started = False

            try:
                self._timeline_tracker.start(
                    "infographic",
                    self._timeline_steps("infographic"),
                    summary="Собираю блок инфографики.",
                    first_message="Проверяю итоговую аналитику и входные файлы.",
                )
                timeline_started = True

                created_at = _now_iso()
                stamp = datetime.now(self.settings.timezone()).strftime("%Y%m%d_%H%M%S")
                run_dir = self._reports_root("infographic") / f"{stamp}_{_file_safe_slug(summary_latest.get('title') or 'infographic')}"
                run_dir.mkdir(parents=True, exist_ok=True)

                google_doc_url = request.google_doc_url.strip()
                if not google_doc_url:
                    raise HTTPException(status_code=400, detail="Укажите ссылку на Google Doc.")

                photo_name, photo_path, photo_url = await self._save_infographic_asset(photo_upload, run_dir, "photo")
                logo_name, logo_path, logo_url = await self._save_infographic_asset(logo_upload, run_dir, "logo")

                current_step = "describe"
                self._timeline_tracker.advance("infographic", current_step, message="Готовлю описания общего фото и логотипа.")
                photo_description = await self._describe_infographic_image(photo_path, "общее фото", slot="photo")
                logo_description = await self._describe_infographic_image(logo_path, "логотип", slot="logo")
                (run_dir / "photo_description.txt").write_text(photo_description, encoding="utf-8")
                (run_dir / "logo_description.txt").write_text(logo_description, encoding="utf-8")

                summary_text = self._read_report_text(summary_latest)
                notebook_title = f"Инфографика {datetime.now(self.settings.timezone()).strftime('%d.%m.%Y %H:%M')}"

                current_step = "notebook"
                self._timeline_tracker.advance("infographic", current_step, message="Создаю отдельный блокнот NotebookLM.")
                notebook = notebooklm.create_notebook(notebook_title, profile=profile)
                notebook_id = str(notebook.get("id") or "").strip()
                if not notebook_id:
                    raise HTTPException(status_code=502, detail="NotebookLM не вернул идентификатор блокнота.")
                notebook_url = notebook.get("url")
                (run_dir / "notebook.json").write_text(json.dumps(notebook, ensure_ascii=False, indent=2), encoding="utf-8")

                current_step = "sources"
                self._timeline_tracker.advance("infographic", current_step, message="Добавляю Google Doc, итоговую аналитику и описания изображений в NotebookLM.")
                source_payloads = [
                    notebooklm.add_source(notebook_id, "auto", google_doc_url, profile=profile, title="Google Doc для инфографики"),
                    notebooklm.add_source(notebook_id, "text", summary_text, profile=profile, title="Итоговая аналитика"),
                    notebooklm.add_source(notebook_id, "text", f"Описание общего фото:\n{photo_description}", profile=profile, title="Описание общего фото"),
                    notebooklm.add_source(notebook_id, "text", f"Описание логотипа:\n{logo_description}", profile=profile, title="Описание логотипа"),
                    notebooklm.add_source(
                        notebook_id,
                        "text",
                        "\n".join(
                            [
                                "Контекст сборки инфографики:",
                                f"- Google Doc: {google_doc_url}",
                                f"- Итоговая аналитика: {summary_latest.get('title') or 'итоговый документ'}",
                                f"- Фото: {photo_name}",
                                f"- Логотип: {logo_name}",
                            ]
                        ),
                        profile=profile,
                        title="Контекст инфографики",
                    ),
                ]
                source_ids = [
                    str(source.get("id") or "").strip()
                    for payload in source_payloads
                    for source in (payload.get("new_sources") or [])
                    if str(source.get("id") or "").strip()
                ]
                sources_snapshot = notebooklm.list_sources(notebook_id, profile=profile)
                (run_dir / "sources.json").write_text(json.dumps(sources_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

                current_step = "artifact"
                self._timeline_tracker.advance("infographic", current_step, message="Запускаю инфографику в NotebookLM.")
                artifact = notebooklm.create_artifact(
                    notebook_id,
                    "infographic",
                    profile=profile,
                    source_ids=source_ids,
                    focus_prompt=block.system_prompt.strip(),
                    language="ru",
                )
                studio_items = list(artifact.get("studio_items") or [])
                (run_dir / "artifact.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
                artifact_raw = artifact.get("raw") if isinstance(artifact.get("raw"), dict) else {}
                artifact_id = str(artifact_raw.get("artifact_id") or "").strip()
                self._timeline_tracker.touch("infographic", current_step, message="Р–РґСѓ РіРѕС‚РѕРІСѓСЋ РёРЅС„РѕРіСЂР°С„РёРєСѓ Рё СЃРѕС…СЂР°РЅСЏСЋ PNG.")
                completed_artifact = notebooklm.wait_for_artifact(
                    notebook_id,
                    "infographic",
                    profile=profile,
                    artifact_id=artifact_id or None,
                )
                studio_items = list(completed_artifact.get("studio_items") or studio_items)
                (run_dir / "artifact_status.json").write_text(
                    json.dumps(completed_artifact, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                image_name = "infographic.png"
                image_path = run_dir / image_name
                capture_result = notebooklm.capture_infographic_image(
                    notebook_url or f"https://notebooklm.google.com/notebook/{notebook_id}",
                    image_path,
                    profile=profile,
                )
                image_url = self._public_download_url(image_path)
                (run_dir / "capture.json").write_text(
                    json.dumps(capture_result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                (run_dir / "request.json").write_text(
                    json.dumps(
                        {
                            "googleDocUrl": google_doc_url,
                            "photoName": photo_name,
                            "logoName": logo_name,
                            "summaryReportCreatedAt": summary_latest.get("createdAt"),
                            "summaryTitle": summary_latest.get("title"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                done_message = "Инфографика запущена в NotebookLM."
                done_message = "РРЅС„РѕРіСЂР°С„РёРєР° СЃРѕС…СЂР°РЅРµРЅР° РєР°Рє PNG."
                timeline = self._timeline_tracker.finish("infographic", "done", message=done_message, summary=done_message)
                self._write_latest_report(
                    "infographic",
                    {
                        "blockId": "infographic",
                        "blockName": block.name,
                        "createdAt": created_at,
                        "notebookId": notebook_id,
                        "notebookTitle": notebook.get("title") or notebook_title,
                        "notebookUrl": notebook_url,
                        "profile": profile,
                        "googleDocUrl": google_doc_url,
                        "summaryReportCreatedAt": summary_latest.get("createdAt"),
                        "summaryTitle": summary_latest.get("title"),
                        "photoName": photo_name,
                        "photoPath": str(photo_path),
                        "photoUrl": photo_url,
                        "logoName": logo_name,
                        "logoPath": str(logo_path),
                        "logoUrl": logo_url,
                        "imageName": image_name,
                        "imagePath": str(image_path),
                        "imageUrl": image_url,
                        "photoDescription": photo_description,
                        "logoDescription": logo_description,
                        "runDir": str(run_dir),
                        "artifactId": artifact_id or None,
                        "studioItems": studio_items,
                        "timeline": timeline.model_dump(),
                    },
                )
                self._timeline_tracker.clear("infographic")

                return AnalyticsInfographicRunResponse(
                    ok=True,
                    block_name=block.name,
                    created_at=created_at,
                    notebook_id=notebook_id,
                    notebook_title=notebook.get("title") or notebook_title,
                    notebook_url=notebook_url,
                    profile=profile,
                    google_doc_url=google_doc_url,
                    photo_name=photo_name,
                    photo_url=photo_url,
                    logo_name=logo_name,
                    logo_url=logo_url,
                    image_name=image_name,
                    image_url=image_url,
                    photo_description=photo_description,
                    logo_description=logo_description,
                    summary_created_at=str(summary_latest.get("createdAt") or ""),
                    summary_title=summary_latest.get("title"),
                    studio_items=studio_items,
                    timeline=timeline,
                )
            except HTTPException as exc:
                if timeline_started:
                    self._timeline_tracker.fail("infographic", current_step, message=str(exc.detail))
                raise
            except Exception as exc:
                if timeline_started:
                    self._timeline_tracker.fail("infographic", current_step, message=str(exc))
                raise HTTPException(status_code=502, detail=f"Не удалось собрать инфографику: {exc}") from exc
