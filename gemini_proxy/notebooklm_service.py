from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from .config import Settings


GOOGLE_DOC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"https?://docs\.google\.com/document/d/([A-Za-z0-9_-]+)"), "doc"),
    (re.compile(r"https?://docs\.google\.com/presentation/d/([A-Za-z0-9_-]+)"), "slides"),
    (re.compile(r"https?://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)"), "sheets"),
    (re.compile(r"https?://drive\.google\.com/file/d/([A-Za-z0-9_-]+)"), "pdf"),
]
YOUTUBE_PATTERN = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/", re.IGNORECASE)
URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)


class NotebookLMService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _imports(self) -> dict[str, Any]:
        try:
            from nlm.core.auth import AuthManager
            from nlm.core.client import NotebookLMClient
            from nlm.core.exceptions import AuthenticationError, NLMError, ProfileNotFoundError
            from nlm.utils.cdp import extract_cookies_via_cdp, terminate_chrome
        except Exception as exc:  # pragma: no cover - defensive import gate
            raise RuntimeError(
                "NotebookLM integration is unavailable. Install dependency 'notebooklm-cli==0.1.12'."
            ) from exc

        return {
            "AuthManager": AuthManager,
            "NotebookLMClient": NotebookLMClient,
            "AuthenticationError": AuthenticationError,
            "NLMError": NLMError,
            "ProfileNotFoundError": ProfileNotFoundError,
            "extract_cookies_via_cdp": extract_cookies_via_cdp,
            "terminate_chrome": terminate_chrome,
        }

    @staticmethod
    def _serialize(value: Any) -> Any:
        if is_dataclass(value):
            data = asdict(value)
        elif isinstance(value, dict):
            data = value
        elif isinstance(value, list):
            return [NotebookLMService._serialize(item) for item in value]
        elif isinstance(value, tuple):
            return [NotebookLMService._serialize(item) for item in value]
        elif isinstance(value, datetime):
            return value.isoformat()
        else:
            return value

        return {str(key): NotebookLMService._serialize(item) for key, item in data.items()}

    def _profile_name(self, profile: str | None) -> str:
        value = (profile or self.settings.notebooklm_default_profile or "default").strip()
        return value or "default"

    @staticmethod
    def _terminate_existing_nlm_chrome() -> None:
        profile_dir = str((Path.home() / ".nlm" / "chrome-profile").resolve())
        escaped = profile_dir.replace("'", "''")
        script = (
            f"$profileDir = '{escaped}'; "
            "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
            "Where-Object { $_.CommandLine -match [regex]::Escape($profileDir) } | "
            "ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception:
            return
        time.sleep(1.5)

    def _profile_snapshot(self, profile_name: str, auth_manager: Any | None = None) -> dict[str, Any]:
        modules = self._imports()
        AuthManager = modules["AuthManager"]
        auth = auth_manager or AuthManager(profile_name)
        metadata: dict[str, Any] = {
            "name": profile_name,
            "exists": auth.profile_exists(),
            "profile_dir": str(auth.profile_dir),
            "email": None,
            "last_validated": None,
        }

        if not auth.profile_exists():
            return metadata

        try:
            profile = auth.load_profile()
        except Exception:
            return metadata

        metadata["email"] = getattr(profile, "email", None)
        last_validated = getattr(profile, "last_validated", None)
        metadata["last_validated"] = (
            last_validated.isoformat() if isinstance(last_validated, datetime) else None
        )
        return metadata

    def list_profiles(self) -> list[dict[str, Any]]:
        modules = self._imports()
        AuthManager = modules["AuthManager"]
        return [
            self._profile_snapshot(profile_name)
            for profile_name in AuthManager.list_profiles()
        ]

    def auth_status(self, profile: str | None = None) -> dict[str, Any]:
        modules = self._imports()
        AuthManager = modules["AuthManager"]
        NotebookLMClient = modules["NotebookLMClient"]
        NLMError = modules["NLMError"]
        profile_name = self._profile_name(profile)
        auth = AuthManager(profile_name)

        base = {
            "ok": True,
            "authenticated": False,
            "profile": profile_name,
            "profile_dir": str(auth.profile_dir),
            "email": None,
            "notebook_count": 0,
            "message": "",
            "profiles": self.list_profiles(),
        }

        if not auth.profile_exists():
            base["message"] = "Profile not found. Run NotebookLM login first."
            return base

        try:
            profile_obj = auth.load_profile()
            base["email"] = getattr(profile_obj, "email", None)
            with NotebookLMClient(profile=profile_name) as client:
                notebooks = client.list_notebooks()
            auth.save_profile(
                cookies=profile_obj.cookies,
                csrf_token=profile_obj.csrf_token,
                session_id=profile_obj.session_id,
                email=profile_obj.email,
            )
        except NLMError as exc:
            base["message"] = exc.message
            base["profiles"] = self.list_profiles()
            return base

        base["authenticated"] = True
        base["notebook_count"] = len(notebooks)
        base["message"] = "NotebookLM session is valid."
        base["profiles"] = self.list_profiles()
        return base

    def login(self, profile: str | None = None, timeout_sec: int = 300) -> dict[str, Any]:
        modules = self._imports()
        AuthManager = modules["AuthManager"]
        extract_cookies_via_cdp = modules["extract_cookies_via_cdp"]
        terminate_chrome = modules["terminate_chrome"]
        profile_name = self._profile_name(profile)
        auth = AuthManager(profile_name)

        existing_email = None
        if auth.profile_exists():
            try:
                existing_email = auth.load_profile().email
            except Exception:
                existing_email = None

        try:
            self._terminate_existing_nlm_chrome()
            result = extract_cookies_via_cdp(
                auto_launch=True,
                wait_for_login=True,
                login_timeout=timeout_sec,
            )
            profile_obj = auth.save_profile(
                cookies=result["cookies"],
                csrf_token=result.get("csrf_token"),
                session_id=result.get("session_id"),
                email=existing_email,
            )
        finally:
            terminate_chrome()

        status = self.auth_status(profile_name)
        status["ok"] = True
        status["authenticated"] = True
        status["profile"] = profile_name
        status["profile_dir"] = str(auth.profile_dir)
        status["email"] = getattr(profile_obj, "email", None)
        status["message"] = "NotebookLM login completed."
        return status

    def list_notebooks(self, profile: str | None = None) -> list[dict[str, Any]]:
        modules = self._imports()
        NotebookLMClient = modules["NotebookLMClient"]
        profile_name = self._profile_name(profile)

        with NotebookLMClient(profile=profile_name) as client:
            notebooks = client.list_notebooks()

        result: list[dict[str, Any]] = []
        for notebook in notebooks:
            item = self._serialize(notebook)
            if hasattr(notebook, "url"):
                item["url"] = notebook.url
            if hasattr(notebook, "ownership"):
                item["ownership"] = notebook.ownership
            result.append(item)
        return result

    def create_notebook(self, title: str, profile: str | None = None) -> dict[str, Any]:
        modules = self._imports()
        NotebookLMClient = modules["NotebookLMClient"]
        profile_name = self._profile_name(profile)
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Notebook title is required.")

        with NotebookLMClient(profile=profile_name) as client:
            notebook = client.create_notebook(clean_title)

        if notebook is None:
            raise RuntimeError("NotebookLM returned an empty result when creating a notebook.")

        payload = self._serialize(notebook)
        if hasattr(notebook, "url"):
            payload["url"] = notebook.url
        if hasattr(notebook, "ownership"):
            payload["ownership"] = notebook.ownership
        return payload

    def list_sources(
        self,
        notebook_id: str,
        profile: str | None = None,
        *,
        include_drive_status: bool = True,
    ) -> list[dict[str, Any]]:
        modules = self._imports()
        NotebookLMClient = modules["NotebookLMClient"]
        profile_name = self._profile_name(profile)

        with NotebookLMClient(profile=profile_name) as client:
            sources = client.list_sources(notebook_id)
            drive_map: dict[str, dict[str, Any]] = {}
            if include_drive_status:
                try:
                    drive_sources = client.list_drive_sources(notebook_id, check_freshness=False)
                except Exception:
                    drive_sources = []
                for drive_source in drive_sources:
                    drive_map[drive_source.id] = self._serialize(drive_source)

        result: list[dict[str, Any]] = []
        for source in sources:
            item = self._serialize(source)
            drive_item = drive_map.get(str(item.get("id", "")))
            if drive_item:
                item["is_stale"] = bool(drive_item.get("is_stale"))
                item["original_type"] = drive_item.get("original_type")
            result.append(item)
        return result

    @staticmethod
    def _extract_google_drive_reference(value: str) -> tuple[str, str] | None:
        for pattern, doc_type in GOOGLE_DOC_PATTERNS:
            match = pattern.search(value)
            if match:
                return match.group(1), doc_type
        return None

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        return bool(URL_PATTERN.match(value.strip()))

    def _resolve_source_input(
        self,
        kind: str,
        value: str,
        title: str | None = None,
        doc_type: str | None = None,
    ) -> dict[str, Any]:
        clean_value = value.strip()
        clean_kind = kind.strip().lower()
        clean_title = (title or "").strip()
        clean_doc_type = (doc_type or "doc").strip().lower() or "doc"

        if not clean_value:
            raise ValueError("Source value is required.")

        if clean_kind == "text":
            return {
                "resolved_kind": "text",
                "value": clean_value,
                "title": clean_title or "Portal Text Source",
                "doc_type": clean_doc_type,
            }

        if clean_kind == "drive":
            return {
                "resolved_kind": "drive",
                "value": clean_value,
                "title": clean_title or f"Drive Source ({clean_value[:8]}...)",
                "doc_type": clean_doc_type,
            }

        if clean_kind == "youtube":
            return {
                "resolved_kind": "youtube",
                "value": clean_value,
                "title": clean_title,
                "doc_type": clean_doc_type,
            }

        if clean_kind == "url":
            return {
                "resolved_kind": "url",
                "value": clean_value,
                "title": clean_title,
                "doc_type": clean_doc_type,
            }

        if clean_kind != "auto":
            raise ValueError(f"Unsupported source kind: {kind}")

        drive_ref = self._extract_google_drive_reference(clean_value)
        if drive_ref is not None:
            document_id, inferred_type = drive_ref
            return {
                "resolved_kind": "drive",
                "value": document_id,
                "title": clean_title or f"Google Drive Source ({document_id[:8]}...)",
                "doc_type": clean_doc_type if doc_type else inferred_type,
            }

        if YOUTUBE_PATTERN.search(clean_value):
            return {
                "resolved_kind": "youtube",
                "value": clean_value,
                "title": clean_title,
                "doc_type": clean_doc_type,
            }

        if self._looks_like_url(clean_value):
            return {
                "resolved_kind": "url",
                "value": clean_value,
                "title": clean_title,
                "doc_type": clean_doc_type,
            }

        return {
            "resolved_kind": "text",
            "value": clean_value,
            "title": clean_title or "Portal Text Source",
            "doc_type": clean_doc_type,
        }

    def _wait_for_new_sources(
        self,
        notebook_id: str,
        profile_name: str,
        baseline_ids: set[str],
        timeout_sec: int,
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + max(3, timeout_sec)
        latest_sources = self.list_sources(notebook_id, profile_name)

        while time.monotonic() < deadline:
            latest_sources = self.list_sources(notebook_id, profile_name)
            if any(str(item.get("id", "")) not in baseline_ids for item in latest_sources):
                return latest_sources
            time.sleep(2)

        return latest_sources

    def add_source(
        self,
        notebook_id: str,
        kind: str,
        value: str,
        *,
        profile: str | None = None,
        title: str | None = None,
        doc_type: str | None = None,
        wait_timeout_sec: int = 30,
    ) -> dict[str, Any]:
        modules = self._imports()
        NotebookLMClient = modules["NotebookLMClient"]
        profile_name = self._profile_name(profile)
        resolved = self._resolve_source_input(kind, value, title, doc_type)
        before_sources = self.list_sources(notebook_id, profile_name)
        before_ids = {str(item.get("id", "")) for item in before_sources}

        with NotebookLMClient(profile=profile_name) as client:
            if resolved["resolved_kind"] in {"url", "youtube"}:
                raw_result = client.add_source_url(notebook_id, resolved["value"])
            elif resolved["resolved_kind"] == "text":
                raw_result = client.add_source_text(
                    notebook_id,
                    resolved["value"],
                    title=resolved["title"],
                )
            elif resolved["resolved_kind"] == "drive":
                raw_result = client.add_source_drive(
                    notebook_id,
                    resolved["value"],
                    resolved["title"],
                    resolved["doc_type"],
                )
            else:  # pragma: no cover - guarded by _resolve_source_input
                raise ValueError(f"Unsupported source kind: {resolved['resolved_kind']}")

        after_sources = self._wait_for_new_sources(
            notebook_id,
            profile_name,
            before_ids,
            wait_timeout_sec,
        )
        new_sources = [
            item for item in after_sources
            if str(item.get("id", "")) not in before_ids
        ]

        return {
            "ok": True,
            "notebook_id": notebook_id,
            "profile": profile_name,
            "requested_kind": kind,
            "resolved_kind": resolved["resolved_kind"],
            "value": resolved["value"],
            "title": resolved["title"],
            "doc_type": resolved["doc_type"],
            "new_sources": new_sources,
            "sources": after_sources,
            "raw": self._serialize(raw_result),
        }

    def query(
        self,
        notebook_id: str,
        prompt: str,
        *,
        profile: str | None = None,
        source_ids: list[str] | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        modules = self._imports()
        NotebookLMClient = modules["NotebookLMClient"]
        profile_name = self._profile_name(profile)
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("Query prompt is required.")

        with NotebookLMClient(profile=profile_name) as client:
            result = client.query(
                notebook_id,
                clean_prompt,
                source_ids=source_ids or None,
                conversation_id=conversation_id or None,
            )

        if result is None:
            raise RuntimeError("NotebookLM returned an empty response to the query.")

        payload = self._serialize(result)
        payload["ok"] = True
        payload["profile"] = profile_name
        payload["notebook_id"] = notebook_id
        payload["prompt"] = clean_prompt
        payload["sources"] = self._serialize(payload.get("sources", []))
        payload["citations"] = {
            str(key): str(value)
            for key, value in dict(payload.get("citations", {})).items()
        }
        return payload

    def studio_status(self, notebook_id: str, profile: str | None = None) -> list[dict[str, Any]]:
        modules = self._imports()
        NotebookLMClient = modules["NotebookLMClient"]
        profile_name = self._profile_name(profile)

        with NotebookLMClient(profile=profile_name) as client:
            items = client.get_studio_status(notebook_id)

        return self._serialize(items)

    def create_artifact(
        self,
        notebook_id: str,
        artifact_type: str,
        *,
        profile: str | None = None,
        source_ids: list[str] | None = None,
        focus_prompt: str | None = None,
        description: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        modules = self._imports()
        NotebookLMClient = modules["NotebookLMClient"]
        profile_name = self._profile_name(profile)
        clean_type = artifact_type.strip().lower()
        selected_source_ids = [item.strip() for item in (source_ids or []) if item.strip()]
        clean_prompt = (focus_prompt or "").strip()
        clean_description = (description or "").strip()
        target_language = (language or self.settings.notebooklm_default_language or "ru").strip() or "ru"

        with NotebookLMClient(profile=profile_name) as client:
            if clean_type == "audio":
                result = client.create_audio(
                    notebook_id,
                    source_ids=selected_source_ids or None,
                    language=target_language,
                    focus_prompt=clean_prompt,
                )
            elif clean_type == "report":
                result = client.create_report(
                    notebook_id,
                    source_ids=selected_source_ids or None,
                    custom_prompt=clean_prompt,
                    language=target_language,
                )
            elif clean_type == "quiz":
                result = client.create_quiz(
                    notebook_id,
                    source_ids=selected_source_ids or None,
                )
            elif clean_type == "flashcards":
                result = client.create_flashcards(
                    notebook_id,
                    source_ids=selected_source_ids or None,
                )
            elif clean_type == "mindmap":
                result = client.create_mindmap(
                    notebook_id,
                    source_ids=selected_source_ids or None,
                )
            elif clean_type == "slides":
                result = client.create_slides(
                    notebook_id,
                    source_ids=selected_source_ids or None,
                    language=target_language,
                    focus_prompt=clean_prompt,
                )
            elif clean_type == "infographic":
                result = client.create_infographic(
                    notebook_id,
                    source_ids=selected_source_ids or None,
                    language=target_language,
                    focus_prompt=clean_prompt,
                )
            elif clean_type == "video":
                result = client.create_video(
                    notebook_id,
                    source_ids=selected_source_ids or None,
                    language=target_language,
                    focus_prompt=clean_prompt,
                )
            elif clean_type == "data-table":
                if not clean_description:
                    clean_description = clean_prompt
                if not clean_description:
                    raise ValueError("Description is required for data-table generation.")
                result = client.create_data_table(
                    notebook_id,
                    clean_description,
                    source_ids=selected_source_ids or None,
                    language=target_language,
                )
            else:
                raise ValueError(f"Unsupported artifact type: {artifact_type}")

        status = self.studio_status(notebook_id, profile_name)
        return {
            "ok": True,
            "profile": profile_name,
            "notebook_id": notebook_id,
            "artifact_type": clean_type,
            "raw": self._serialize(result),
            "studio_items": status,
        }

    def wait_for_artifact(
        self,
        notebook_id: str,
        artifact_type: str,
        *,
        profile: str | None = None,
        artifact_id: str | None = None,
        timeout_sec: int = 420,
        poll_interval_sec: int = 5,
    ) -> dict[str, Any]:
        profile_name = self._profile_name(profile)
        clean_type = artifact_type.strip().lower()
        clean_artifact_id = (artifact_id or "").strip()
        deadline = time.monotonic() + max(30, timeout_sec)
        last_items: list[dict[str, Any]] = []

        while time.monotonic() < deadline:
            last_items = self.studio_status(notebook_id, profile=profile_name)
            matched = next(
                (
                    item
                    for item in last_items
                    if (
                        clean_artifact_id
                        and str(item.get("artifact_id") or item.get("id") or "").strip() == clean_artifact_id
                    )
                    or (
                        not clean_artifact_id
                        and str(item.get("type") or "").strip().lower() == clean_type
                    )
                ),
                None,
            )
            if matched and str(matched.get("status") or "").strip().lower() == "completed":
                return {
                    "ok": True,
                    "profile": profile_name,
                    "notebook_id": notebook_id,
                    "artifact": matched,
                    "studio_items": last_items,
                }

            time.sleep(max(1, poll_interval_sec))

        raise RuntimeError(
            f"NotebookLM did not finish the '{clean_type}' artifact in {timeout_sec} seconds."
        )

    def capture_infographic_image(
        self,
        notebook_url: str,
        output_path: str | Path,
        *,
        profile: str | None = None,
        timeout_sec: int = 420,
    ) -> dict[str, Any]:
        modules = self._imports()
        AuthManager = modules["AuthManager"]
        profile_name = self._profile_name(profile)
        auth = AuthManager(profile_name)
        if not auth.profile_exists():
            raise RuntimeError(f"NotebookLM profile '{profile_name}' is not authenticated.")

        script_path = (self.settings.project_root() / "tools" / "notebooklm_capture_infographic.mjs").resolve()
        if not script_path.is_file():
            raise RuntimeError(f"NotebookLM capture script not found: {script_path}")

        target = Path(output_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "notebookUrl": notebook_url,
            "cookiesPath": str(auth.cookies_file.resolve()),
            "outputPath": str(target),
            "chromeExecutablePath": self.settings.chrome_executable_path,
            "timeoutSec": max(30, timeout_sec),
        }
        completed = subprocess.run(
            [self.settings.node_command, str(script_path)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=max(90, timeout_sec + 60),
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "NotebookLM capture failed."
            raise RuntimeError(message)

        try:
            parsed = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("NotebookLM capture returned invalid JSON.") from exc

        if not target.is_file():
            raise RuntimeError(f"NotebookLM capture did not create image: {target}")

        parsed["ok"] = True
        parsed["profile"] = profile_name
        parsed["outputPath"] = str(target)
        return parsed
