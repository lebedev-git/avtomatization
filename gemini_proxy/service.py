from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
import random
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from gemini_webapi import ChatSession, GeminiClient, GeminiError
from gemini_webapi.constants import GRPC, Model
from gemini_webapi.types import Candidate, Image, RPCData
from gemini_webapi.utils.upload_file import parse_file_name, upload_file

from .config import Settings
from .schemas import CandidatePayload, GenerateRequest, GenerateResponse, ImagePayload


class GeminiProxyService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: GeminiClient | None = None
        self._lock = asyncio.Lock()
        self._generate_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def is_initialized(self) -> bool:
        return self._client is not None and self._client._running  # type: ignore[attr-defined]

    async def ensure_client(self) -> GeminiClient:
        if self.is_initialized():
            return self._client  # type: ignore[return-value]

        async with self._lock:
            if self.is_initialized():
                return self._client  # type: ignore[return-value]

            cookies = self._build_cookie_map()
            if not cookies and not self.settings.gemini_allow_browser_cookie_fallback:
                raise RuntimeError(
                    "No cookies configured. Set GEMINI_SECURE_1PSID / "
                    "GEMINI_SECURE_1PSIDTS or GEMINI_COOKIE_JSON_PATH."
                )

            client = GeminiClient(
                proxy=self.settings.gemini_proxy,
                cookies=cookies or None,
                verify=self.settings.gemini_verify_ssl,
            )
            await client.init(
                timeout=self.settings.gemini_timeout_sec,
                auto_close=self.settings.gemini_auto_close,
                close_delay=self.settings.gemini_close_delay_sec,
                auto_refresh=self.settings.gemini_auto_refresh,
                refresh_interval=self.settings.gemini_refresh_interval_sec,
                verbose=self.settings.gemini_verbose,
                watchdog_timeout=self.settings.gemini_watchdog_timeout_sec,
            )
            self._client = client
            return client

    async def auth_check(self) -> dict[str, Any]:
        client = await self.ensure_client()
        cookie_names = sorted(
            {
                cookie.name
                for cookie in client.cookies.jar
                if "google.com" in (cookie.domain or "")
            }
        )
        return {
            "ok": True,
            "initialized": True,
            "auth_sources_hint": self.settings.auth_sources_hint(),
            "cookie_json_path": (
                str(self.settings.cookie_json_file())
                if self.settings.cookie_json_file()
                else None
            ),
            "active_cookie_names": cookie_names,
            "build_label": client.build_label,
            "session_id": client.session_id,
        }

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        client = await self.ensure_client()
        files = await self._prepare_files(request.files)

        model_value: Model | str = Model.UNSPECIFIED
        if request.model:
            model_value = request.model

        request_kwargs: dict[str, Any] = {}
        if request.timeout_sec is not None:
            request_kwargs["timeout"] = request.timeout_sec

        async with self._generate_lock:
            original_timeout = client.timeout
            original_watchdog_timeout = client.watchdog_timeout
            if request.timeout_sec is not None:
                client.timeout = request.timeout_sec
            if request.watchdog_timeout_sec is not None:
                client.watchdog_timeout = request.watchdog_timeout_sec

            try:
                chat: ChatSession | None = None
                if request.chat_metadata:
                    chat = client.start_chat(
                        metadata=request.chat_metadata,
                        model=model_value,
                        gem=request.gem,
                    )
                    output = await chat.send_message(
                        request.prompt,
                        files=files or None,
                        temporary=request.temporary,
                        **request_kwargs,
                    )
                elif request.disable_internal_retry:
                    output = await self._generate_content_once(
                        client,
                        prompt=request.prompt,
                        files=files or None,
                        model=model_value,
                        gem=request.gem,
                        temporary=request.temporary,
                        request_kwargs=request_kwargs,
                    )
                else:
                    output = await client.generate_content(
                        request.prompt,
                        files=files or None,
                        model=model_value,
                        gem=request.gem,
                        temporary=request.temporary,
                        **request_kwargs,
                    )
            finally:
                client.timeout = original_timeout
                client.watchdog_timeout = original_watchdog_timeout

        chosen_images = await self._process_images(
            output.images,
            request=request,
            prefix=request.image_filename_prefix or "image",
        )

        candidates: list[CandidatePayload] = []
        for index, candidate in enumerate(output.candidates):
            candidate_images = chosen_images if index == output.chosen else []
            candidates.append(
                CandidatePayload(
                    rcid=candidate.rcid,
                    text=candidate.text,
                    thoughts=candidate.thoughts,
                    images=candidate_images,
                )
            )

        return GenerateResponse(
            metadata=output.metadata,
            chosen=output.chosen,
            text=output.text,
            thoughts=output.thoughts,
            images=chosen_images,
            candidates=candidates,
            raw={
                "candidate_count": len(output.candidates),
                "has_images": bool(output.images),
            },
        )

    def supported_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        for model in Model:
            models.append(
                {
                    "name": model.model_name,
                    "advanced_only": model.advanced_only,
                    "predefined": model is not Model.UNSPECIFIED,
                }
            )
        return models

    def _build_cookie_map(self) -> dict[str, str]:
        cookies: dict[str, str] = {}

        cookie_json_file = self.settings.cookie_json_file()
        if cookie_json_file:
            cookies.update(self._load_cookie_json(cookie_json_file))

        if self.settings.gemini_secure_1psid:
            cookies["__Secure-1PSID"] = self.settings.gemini_secure_1psid
        if self.settings.gemini_secure_1psidts:
            cookies["__Secure-1PSIDTS"] = self.settings.gemini_secure_1psidts

        return cookies

    def _load_cookie_json(self, path: Path) -> dict[str, str]:
        if not path.is_file():
            raise FileNotFoundError(f"Cookie JSON file not found: {path}")

        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return {
                str(name): str(value)
                for name, value in payload.items()
                if isinstance(name, str) and value not in (None, "")
            }

        if isinstance(payload, list):
            cookies: dict[str, str] = {}
            for item in payload:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                value = item.get("value")
                domain = str(item.get("domain", ""))
                if not name or value in (None, ""):
                    continue
                if domain and "google.com" not in domain and "gemini.google.com" not in domain:
                    continue
                cookies[str(name)] = str(value)
            return cookies

        raise ValueError(
            "Cookie JSON must be either a name/value object or a browser-export array."
        )

    async def _prepare_files(self, files: list[Any]) -> list[Path | bytes]:
        prepared: list[Path | bytes] = []
        for file in files:
            if file.path:
                file_path = Path(file.path).expanduser()
                if not file_path.is_absolute():
                    file_path = (self.settings.project_root() / file_path).resolve()
                if not file_path.is_file():
                    raise FileNotFoundError(f"Input file not found: {file_path}")
                prepared.append(file_path)
                continue

            if file.base64_data:
                prepared.append(base64.b64decode(file.base64_data))
                continue

            if file.url:
                async with httpx.AsyncClient(
                    timeout=self.settings.gemini_timeout_sec,
                    verify=self.settings.gemini_verify_ssl,
                    proxy=self.settings.gemini_proxy,
                ) as http_client:
                    response = await http_client.get(file.url)
                    response.raise_for_status()
                    prepared.append(response.content)
                continue

        return prepared

    async def _generate_content_once(
        self,
        client: GeminiClient,
        *,
        prompt: str,
        files: list[Path | bytes] | None,
        model: Model | str,
        gem: str | None,
        temporary: bool,
        request_kwargs: dict[str, Any],
    ):
        raw_generate = getattr(GeminiClient._generate, "__wrapped__", None)
        if raw_generate is None:
            return await client.generate_content(
                prompt,
                files=files,
                model=model,
                gem=gem,
                temporary=temporary,
                **request_kwargs,
            )

        if client.auto_close:
            await client.reset_close_task()

        client._reqid = random.randint(10000, 99999)

        file_data = None
        if files:
            await client._batch_execute(
                [
                    RPCData(
                        rpcid=GRPC.BARD_ACTIVITY,
                        payload='[[["bard_activity_enabled"]]]',
                    )
                ]
            )
            uploaded_urls = await asyncio.gather(
                *(upload_file(file, client.proxy) for file in files)
            )
            file_data = [
                [[url], parse_file_name(file)]
                for url, file in zip(uploaded_urls, files)
            ]

        try:
            await client._batch_execute(
                [
                    RPCData(
                        rpcid=GRPC.BARD_ACTIVITY,
                        payload='[[["bard_activity_enabled"]]]',
                    )
                ]
            )
            session_state = {
                "last_texts": {},
                "last_thoughts": {},
                "last_progress_time": time.time(),
            }
            output = None
            async for output in raw_generate(
                client,
                prompt=prompt,
                req_file_data=file_data,
                model=model,
                gem=gem,
                chat=None,
                temporary=temporary,
                session_state=session_state,
                **request_kwargs,
            ):
                pass

            if output is None:
                raise GeminiError(
                    "Failed to generate contents. No output data found in response."
                )

            return output
        finally:
            if files:
                for file in files:
                    if isinstance(file, io.BytesIO):
                        file.close()

    async def _process_images(
        self,
        images: list[Image],
        request: GenerateRequest,
        prefix: str,
    ) -> list[ImagePayload]:
        should_save = request.save_images or request.include_image_base64
        output_dir = self._resolve_output_dir(request.image_output_subdir)

        payloads: list[ImagePayload] = []
        for index, image in enumerate(images, start=1):
            saved_path: str | None = None
            image_base64: str | None = None

            if should_save:
                filename = f"{prefix}_{index:02d}{self._guess_extension(image.url)}"
                saved_path = await image.save(
                    path=str(output_dir),
                    filename=filename,
                    verbose=self.settings.gemini_verbose,
                )
                if request.include_image_base64 and saved_path:
                    image_base64 = base64.b64encode(
                        Path(saved_path).read_bytes()
                    ).decode("ascii")

            payloads.append(
                ImagePayload(
                    kind=type(image).__name__,
                    title=image.title,
                    alt=image.alt,
                    url=image.url,
                    saved_path=saved_path,
                    base64_data=image_base64,
                )
            )

        return payloads

    def _resolve_output_dir(self, subdir: str | None) -> Path:
        base_dir = self.settings.downloads_root()
        relative = Path(subdir or self.settings.default_image_subdir)
        target = (base_dir / relative).resolve()

        try:
            target.relative_to(base_dir)
        except ValueError as exc:
            raise ValueError("image_output_subdir must stay inside downloads_dir") from exc

        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def _guess_extension(url: str) -> str:
        path = urlparse(url).path
        suffix = Path(path).suffix.lower()
        if suffix and len(suffix) <= 5:
            return suffix
        return ".png"
