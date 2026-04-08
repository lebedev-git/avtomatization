from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .config import Settings
from .schemas import (
    WebGenerateRequest,
    WebGenerateResponse,
    WebImagePayload,
    WebLoginRequest,
    WebLoginResponse,
)


class GeminiWebRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = asyncio.Lock()

    async def run(self, request: WebGenerateRequest) -> WebGenerateResponse:
        fixed_mode = "pro"
        payload = {
            "action": "run",
            "prompt": request.prompt,
            "mode": fixed_mode,
            "imageTool": request.image_tool,
            "filePath": request.file_path,
            "headless": request.headless,
            "timeoutSec": request.timeout_sec,
            "waitAfterSubmitSec": request.wait_after_submit_sec,
            "captureLabel": request.capture_label,
            "chromeExecutablePath": self.settings.chrome_executable_path,
            "capturesDir": str(self.settings.captures_root()),
            "profileDir": str(self.settings.web_profile_root()),
        }

        data = await self._execute(payload)
        images = [
            WebImagePayload(
                src=item["src"],
                alt=item.get("alt"),
                saved_path=item.get("savedPath"),
                saved_url=self._public_url(item.get("savedPath")),
            )
            for item in data.get("images", [])
        ]

        return WebGenerateResponse(
            ok=bool(data.get("ok", False)),
            mode=fixed_mode,
            mode_actual=data.get("modeActual"),
            image_tool_requested=bool(data.get("imageToolRequested", request.image_tool)),
            image_tool_active=bool(data.get("imageToolActive", False)),
            prompt=str(data.get("prompt", request.prompt)),
            assistant_text=data.get("assistantText"),
            thought_text=data.get("thoughtText"),
            last_turn_text=data.get("lastTurnText"),
            capture_dir=data.get("captureDir"),
            before_capture_path=data.get("beforeCapturePath"),
            after_capture_path=data.get("afterCapturePath"),
            before_capture_url=self._public_url(data.get("beforeCapturePath")),
            after_capture_url=self._public_url(data.get("afterCapturePath")),
            stream_response_path=data.get("streamResponsePath"),
            stream_response_url=self._public_url(data.get("streamResponsePath")),
            stream_request_summary=data.get("streamRequestSummary"),
            stream_response_excerpt=data.get("streamResponseExcerpt"),
            images=images,
            notes=[str(item) for item in data.get("notes", [])],
        )

    async def login(self, request: WebLoginRequest) -> WebLoginResponse:
        payload = {
            "action": "login",
            "headless": request.headless,
            "timeoutSec": request.timeout_sec,
            "chromeExecutablePath": self.settings.chrome_executable_path,
            "profileDir": str(self.settings.web_profile_root()),
        }

        data = await self._execute(payload)
        return WebLoginResponse(
            ok=bool(data.get("ok", False)),
            signed_in=bool(data.get("signedIn", False)),
            already_signed_in=bool(data.get("alreadySignedIn", False)),
            profile_dir=str(data.get("profileDir", self.settings.web_profile_root())),
            message=str(data.get("message", "")),
            current_url=data.get("currentUrl"),
        )

    @staticmethod
    def _clean_runner_error(raw_message: str) -> str:
        message = (raw_message or "").strip()
        if "Gemini web session is not signed in" in message:
            return (
                "Профиль Gemini не авторизован. Нажми 'Войти в Gemini', "
                "заверши вход в открывшемся окне и запусти еще раз."
            )
        if "Gemini web session expired during generation" in message:
            return (
                "Профиль Gemini потерял сессию во время генерации. "
                "Открой вход заново и повтори запуск."
            )
        if "Gemini Pro is not available for the current account" in message:
            return (
                "Режим Pro недоступен для текущего аккаунта Gemini. "
                "Проверь подписку или войди под другим аккаунтом."
            )
        if "temporarily blocked this automated request (1060)" in message.lower():
            return (
                "Google временно заблокировал автоматизированный запрос Gemini (1060). "
                "Нужно подождать и повторить запуск позже."
            )
        if "google blocked or challenged the gemini web session" in message.lower():
            return (
                "Google показал проверку или временно ограничил Gemini до появления поля ввода. "
                "Открой вход в Gemini вручную, заверши проверку в профиле и повтори запуск."
            )
        if "Gemini stayed in mode" in message:
            return (
                "Не удалось переключить Gemini в режим Pro. "
                "Проверь, доступен ли Pro для этого аккаунта."
            )

        first_line = next((line.strip() for line in message.splitlines() if line.strip()), "")
        if first_line.startswith("Error: "):
            first_line = first_line.removeprefix("Error: ").strip()
        return first_line or "Gemini web runner failed"

    async def _execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            completed = await asyncio.create_subprocess_exec(
                self.settings.node_command,
                str(self.settings.web_runner_script()),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.settings.project_root()),
            )

            stdout, stderr = await completed.communicate(
                json.dumps(payload).encode("utf-8")
            )

        if completed.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            if not message:
                message = stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(self._clean_runner_error(message))

        return json.loads(stdout.decode("utf-8"))

    def _public_url(self, raw_path: str | None) -> str | None:
        if not raw_path:
            return None

        target = Path(raw_path).resolve()
        for root, prefix in (
            (self.settings.captures_root(), "/captures"),
            (self.settings.downloads_root(), "/downloads"),
        ):
            try:
                relative = target.relative_to(root)
            except ValueError:
                continue
            return f"{prefix}/{relative.as_posix()}"

        return None
