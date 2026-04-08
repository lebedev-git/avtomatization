from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import json
from pathlib import Path
import urllib.error
import urllib.request

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from gemini_webapi import APIError, AuthError, GeminiError, TemporarilyBlocked, TimeoutError as GeminiTimeoutError, UsageLimitExceeded

from .analytics_multi_agent import AnalyticsAgentService
from .config import get_settings
from .notebooklm_service import NotebookLMService
from .protocol_agent_runtime import ProtocolAgentService
from .schemas import (
    AgentListResponse,
    AnalyticsAgentConfig,
    AnalyticsDay1HistoryResponse,
    AnalyticsDay1RunRequest,
    AnalyticsDay1RunResponse,
    AnalyticsDay2HistoryResponse,
    AnalyticsDay2RunRequest,
    AnalyticsDay2RunResponse,
    AnalyticsInfographicRunRequest,
    AnalyticsInfographicRunResponse,
    AnalyticsResetResponse,
    AnalyticsSourceStatusResponse,
    AnalyticsSummaryRunRequest,
    AnalyticsSummaryRunResponse,
    AnalyticsSummaryStateResponse,
    AuthCheckResponse,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    NotebookLMAddSourceRequest,
    NotebookLMAddSourceResponse,
    NotebookLMArtifactCreateRequest,
    NotebookLMArtifactCreateResponse,
    NotebookLMAuthStatusResponse,
    NotebookLMCreateNotebookRequest,
    NotebookLMCreateNotebookResponse,
    NotebookLMLoginRequest,
    NotebookLMNotebookListResponse,
    NotebookLMQueryRequest,
    NotebookLMQueryResponse,
    NotebookLMSourceListResponse,
    NotebookLMStudioStatusResponse,
    ProtocolAgentConfig,
    ProtocolReportState,
    ProtocolResetResponse,
    ProtocolRunResponse,
    UpdateAnalyticsAgentConfigRequest,
    UpdateProtocolAgentConfigRequest,
    WebGenerateRequest,
    WebGenerateResponse,
    WebLoginRequest,
    WebLoginResponse,
)
from .service import GeminiProxyService
from .web_runner import GeminiWebRunner

settings = get_settings()
service = GeminiProxyService(settings)
web_runner = GeminiWebRunner(settings)
notebooklm = NotebookLMService(settings)
analytics_agent = AnalyticsAgentService(settings, web_runner, service, notebooklm)
protocol_agent = ProtocolAgentService(settings, service, web_runner)

settings.downloads_root().mkdir(parents=True, exist_ok=True)
settings.captures_root().mkdir(parents=True, exist_ok=True)
settings.agents_root().mkdir(parents=True, exist_ok=True)
settings.n8n_inbox_root().mkdir(parents=True, exist_ok=True)
settings.web_profile_root().mkdir(parents=True, exist_ok=True)


def _latest_json_file(search_roots: list[Path]) -> Path | None:
    candidates: list[Path] = []
    seen: set[Path] = set()

    for root in search_roots:
        if not root.exists():
            continue

        for candidate in root.rglob("*.json"):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue

            if resolved in seen or not candidate.is_file():
                continue

            seen.add(resolved)
            candidates.append(candidate)

    if not candidates:
        return None

    return max(candidates, key=lambda item: item.stat().st_mtime)


def _read_json_like_file(target: Path) -> str:
    try:
        raw = target.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw = target.read_text(encoding="utf-8", errors="replace")

    raw = raw.lstrip("\ufeff").strip()
    if not raw:
        raise HTTPException(status_code=400, detail=f"Файл {target.name} пустой.")

    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return raw


def _fetch_remote_n8n_json() -> dict | None:
    url = (settings.n8n_latest_json_url or "").strip()
    if not url:
        return None

    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read().decode(charset, errors="replace").strip()
    except urllib.error.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"n8n webhook вернул ошибку {exc.code}.",
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось достучаться до n8n webhook: {exc.reason}",
        ) from exc

    if not raw:
        raise HTTPException(status_code=502, detail="n8n webhook вернул пустой ответ.")

    try:
        parsed = json.loads(raw)
        content = json.dumps(parsed, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        parsed = None
        content = raw

    return {
        "name": "latest-from-n8n.json",
        "path": url,
        "content": content,
        "raw": parsed,
    }


def _notebooklm_error_detail(exc: Exception) -> str:
    message = getattr(exc, "message", None) or str(exc)
    hint = getattr(exc, "hint", None)
    if hint and hint not in str(message):
        return f"{message} {hint}".strip()
    return str(message).strip()


def _raise_notebooklm_http(exc: Exception) -> None:
    detail = _notebooklm_error_detail(exc)
    name = exc.__class__.__name__

    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=detail) from exc
    if name in {"AuthenticationError"}:
        raise HTTPException(status_code=401, detail=detail) from exc
    if name in {"ProfileNotFoundError", "NotFoundError"}:
        raise HTTPException(status_code=404, detail=detail) from exc
    if name in {"ValidationError", "NLMError"}:
        raise HTTPException(status_code=400, detail=detail) from exc
    if name in {"RateLimitError"}:
        raise HTTPException(status_code=429, detail=detail) from exc
    if name in {"NetworkError"}:
        raise HTTPException(status_code=502, detail=detail) from exc
    if isinstance(exc, RuntimeError):
        raise HTTPException(status_code=500, detail=detail) from exc
    raise HTTPException(status_code=500, detail=detail) from exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await service.close()


app = FastAPI(
    title="Gemini Web Proxy",
    version="0.1.0",
    description="Local reverse-engineered proxy for Gemini web sessions.",
    lifespan=lifespan,
)

app.mount("/downloads", StaticFiles(directory=settings.downloads_root()), name="downloads")
app.mount("/captures", StaticFiles(directory=settings.captures_root()), name="captures")
app.mount("/ui", StaticFiles(directory=Path(__file__).with_name("ui")), name="ui")


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/web-playground")


@app.get("/web-playground", include_in_schema=False)
async def web_playground() -> FileResponse:
    html_path = Path(__file__).with_name("web_playground.html")
    return FileResponse(html_path, media_type="text/html; charset=utf-8")


@app.get("/web-playground.js", include_in_schema=False)
async def web_playground_js() -> FileResponse:
    js_path = Path(__file__).with_name("web_playground.js")
    return FileResponse(js_path, media_type="application/javascript; charset=utf-8")


@app.get("/invoice_main", include_in_schema=False)
async def invoice_main() -> FileResponse:
    html_path = Path(__file__).with_name("web_playground.html")
    return FileResponse(html_path, media_type="text/html; charset=utf-8")


@app.get("/admin", include_in_schema=False)
async def admin_panel() -> FileResponse:
    html_path = Path(__file__).with_name("admin_panel.html")
    return FileResponse(html_path, media_type="text/html; charset=utf-8")


@app.get("/notebooklm-admin", include_in_schema=False)
async def notebooklm_admin() -> FileResponse:
    html_path = Path(__file__).with_name("notebooklm_admin.html")
    return FileResponse(html_path, media_type="text/html; charset=utf-8")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        initialized=service.is_initialized(),
        downloads_dir=str(settings.downloads_root()),
        auth_sources_hint=settings.auth_sources_hint(),
    )


@app.post("/auth/check", response_model=AuthCheckResponse)
async def auth_check() -> AuthCheckResponse:
    try:
        payload = await service.auth_check()
        return AuthCheckResponse(**payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/models")
async def models() -> dict:
    return {
        "models": service.supported_models(),
        "note": (
            "This library ships a small predefined model list. "
            "Your Gemini web account may expose more capabilities than shown here."
        ),
    }


@app.get("/agents", response_model=AgentListResponse)
async def agents() -> AgentListResponse:
    return AgentListResponse(agents=[analytics_agent.agent_summary(), protocol_agent.agent_summary()])


@app.get("/agents/analytics-note/config", response_model=AnalyticsAgentConfig)
async def analytics_note_config() -> AnalyticsAgentConfig:
    return analytics_agent.load_config()


@app.post("/agents/analytics-note/config", response_model=AnalyticsAgentConfig)
async def analytics_note_update_config(
    request: UpdateAnalyticsAgentConfigRequest,
) -> AnalyticsAgentConfig:
    return await analytics_agent.update_config(request)


@app.get("/agents/analytics-note/day1/history", response_model=AnalyticsDay1HistoryResponse)
async def analytics_note_day1_history() -> AnalyticsDay1HistoryResponse:
    return await analytics_agent.day1_history()


@app.get("/agents/analytics-note/day2/history", response_model=AnalyticsDay2HistoryResponse)
async def analytics_note_day2_history() -> AnalyticsDay2HistoryResponse:
    return await analytics_agent.day2_history()


@app.get("/agents/analytics-note/summary/state", response_model=AnalyticsSummaryStateResponse)
async def analytics_note_summary_state() -> AnalyticsSummaryStateResponse:
    return await analytics_agent.summary_state()


@app.get("/agents/analytics-note/source-status", response_model=AnalyticsSourceStatusResponse)
async def analytics_note_source_status() -> AnalyticsSourceStatusResponse:
    return await analytics_agent.source_status()


@app.post("/agents/analytics-note/reset", response_model=AnalyticsResetResponse)
async def analytics_note_reset() -> AnalyticsResetResponse:
    return analytics_agent.reset_state()


@app.post("/agents/analytics-note/day1/run", response_model=AnalyticsDay1RunResponse)
async def analytics_note_day1_run(
    request: AnalyticsDay1RunRequest,
) -> AnalyticsDay1RunResponse:
    return await analytics_agent.run_day1(request)


@app.post("/agents/analytics-note/day2/run", response_model=AnalyticsDay2RunResponse)
async def analytics_note_day2_run(
    request: AnalyticsDay2RunRequest,
) -> AnalyticsDay2RunResponse:
    return await analytics_agent.run_day2(request)


@app.post("/agents/analytics-note/summary/run", response_model=AnalyticsSummaryRunResponse)
async def analytics_note_summary_run(
    request: AnalyticsSummaryRunRequest,
) -> AnalyticsSummaryRunResponse:
    return await analytics_agent.run_summary(request)


@app.post("/agents/analytics-note/infographic/run", response_model=AnalyticsInfographicRunResponse)
async def analytics_note_infographic_run(
    google_doc_url: str = Form(...),
    photo: UploadFile = File(...),
    logo: UploadFile = File(...),
) -> AnalyticsInfographicRunResponse:
    return await analytics_agent.run_infographic(
        AnalyticsInfographicRunRequest(google_doc_url=google_doc_url),
        photo,
        logo,
    )


@app.get("/agents/protocol/config", response_model=ProtocolAgentConfig)
async def protocol_config() -> ProtocolAgentConfig:
    return protocol_agent.load_config()


@app.post("/agents/protocol/config", response_model=ProtocolAgentConfig)
async def protocol_update_config(
    request: UpdateProtocolAgentConfigRequest,
) -> ProtocolAgentConfig:
    return await protocol_agent.update_config(request)


@app.get("/agents/protocol/state", response_model=ProtocolReportState)
async def protocol_state() -> ProtocolReportState:
    return protocol_agent.state()


@app.post("/agents/protocol/reset", response_model=ProtocolResetResponse)
async def protocol_reset() -> ProtocolResetResponse:
    return protocol_agent.reset_state()


@app.post("/agents/protocol/run", response_model=ProtocolRunResponse)
async def protocol_run(
    file: UploadFile = File(...),
) -> ProtocolRunResponse:
    return await protocol_agent.run(file)


@app.get("/n8n/latest-json")
async def n8n_latest_json() -> dict:
    remote = _fetch_remote_n8n_json()
    if remote is not None:
        return remote

    target = _latest_json_file([settings.n8n_inbox_root()])
    if target is None:
        raise HTTPException(
            status_code=404,
            detail="Не нашел ни одного JSON от n8n: ни по webhook, ни в n8n-inbox.",
        )

    return {
        "name": target.name,
        "path": str(target),
        "content": _read_json_like_file(target),
    }


@app.post("/n8n/ingest-json")
async def n8n_ingest_json(request: Request) -> dict:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Тело запроса не является JSON.") from exc

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = settings.n8n_inbox_root() / f"n8n_payload_{stamp}.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    latest_path = settings.n8n_inbox_root() / "latest.json"
    latest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "name": target.name,
        "path": str(target),
        "latest_path": str(latest_path),
    }


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest) -> GenerateResponse:
    try:
        return await service.generate(request)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except UsageLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except TemporarilyBlocked as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GeminiTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except (APIError, GeminiError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/generate-image", response_model=GenerateResponse)
async def generate_image(request: GenerateRequest) -> GenerateResponse:
    payload = request.model_copy(update={"save_images": True})
    return await generate(payload)


@app.post("/generate-web", response_model=WebGenerateResponse)
async def generate_web(request: WebGenerateRequest) -> WebGenerateResponse:
    try:
        return await web_runner.run(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/web-login", response_model=WebLoginResponse)
async def web_login(request: WebLoginRequest) -> WebLoginResponse:
    try:
        return await web_runner.login(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/notebooklm/auth/status", response_model=NotebookLMAuthStatusResponse)
async def notebooklm_auth_status(profile: str | None = None) -> NotebookLMAuthStatusResponse:
    try:
        payload = notebooklm.auth_status(profile)
        return NotebookLMAuthStatusResponse(**payload)
    except Exception as exc:
        _raise_notebooklm_http(exc)


@app.post("/notebooklm/login", response_model=NotebookLMAuthStatusResponse)
async def notebooklm_login(request: NotebookLMLoginRequest) -> NotebookLMAuthStatusResponse:
    try:
        payload = notebooklm.login(profile=request.profile, timeout_sec=request.timeout_sec)
        return NotebookLMAuthStatusResponse(**payload)
    except Exception as exc:
        _raise_notebooklm_http(exc)


@app.get("/notebooklm/notebooks", response_model=NotebookLMNotebookListResponse)
async def notebooklm_notebooks(profile: str | None = None) -> NotebookLMNotebookListResponse:
    try:
        profile_name = notebooklm._profile_name(profile)
        notebooks = notebooklm.list_notebooks(profile_name)
        return NotebookLMNotebookListResponse(profile=profile_name, notebooks=notebooks)
    except Exception as exc:
        _raise_notebooklm_http(exc)


@app.post("/notebooklm/notebooks", response_model=NotebookLMCreateNotebookResponse)
async def notebooklm_create_notebook(
    request: NotebookLMCreateNotebookRequest,
) -> NotebookLMCreateNotebookResponse:
    try:
        notebook = notebooklm.create_notebook(request.title, profile=request.profile)
        return NotebookLMCreateNotebookResponse(ok=True, profile=request.profile, notebook=notebook)
    except Exception as exc:
        _raise_notebooklm_http(exc)


@app.get("/notebooklm/sources", response_model=NotebookLMSourceListResponse)
async def notebooklm_sources(
    notebook_id: str,
    profile: str | None = None,
) -> NotebookLMSourceListResponse:
    try:
        profile_name = notebooklm._profile_name(profile)
        sources = notebooklm.list_sources(notebook_id, profile=profile_name)
        return NotebookLMSourceListResponse(
            profile=profile_name,
            notebook_id=notebook_id,
            sources=sources,
        )
    except Exception as exc:
        _raise_notebooklm_http(exc)


@app.post("/notebooklm/sources", response_model=NotebookLMAddSourceResponse)
async def notebooklm_add_source(
    request: NotebookLMAddSourceRequest,
) -> NotebookLMAddSourceResponse:
    try:
        payload = notebooklm.add_source(
            request.notebook_id,
            request.kind,
            request.value,
            profile=request.profile,
            title=request.title,
            doc_type=request.doc_type,
            wait_timeout_sec=request.wait_timeout_sec,
        )
        return NotebookLMAddSourceResponse(**payload)
    except Exception as exc:
        _raise_notebooklm_http(exc)


@app.post("/notebooklm/query", response_model=NotebookLMQueryResponse)
async def notebooklm_query(request: NotebookLMQueryRequest) -> NotebookLMQueryResponse:
    try:
        payload = notebooklm.query(
            request.notebook_id,
            request.prompt,
            profile=request.profile,
            source_ids=request.source_ids,
            conversation_id=request.conversation_id,
        )
        return NotebookLMQueryResponse(**payload)
    except Exception as exc:
        _raise_notebooklm_http(exc)


@app.get("/notebooklm/studio/status", response_model=NotebookLMStudioStatusResponse)
async def notebooklm_studio_status(
    notebook_id: str,
    profile: str | None = None,
) -> NotebookLMStudioStatusResponse:
    try:
        profile_name = notebooklm._profile_name(profile)
        items = notebooklm.studio_status(notebook_id, profile=profile_name)
        return NotebookLMStudioStatusResponse(
            profile=profile_name,
            notebook_id=notebook_id,
            items=items,
        )
    except Exception as exc:
        _raise_notebooklm_http(exc)


@app.post("/notebooklm/artifacts", response_model=NotebookLMArtifactCreateResponse)
async def notebooklm_create_artifact(
    request: NotebookLMArtifactCreateRequest,
) -> NotebookLMArtifactCreateResponse:
    try:
        payload = notebooklm.create_artifact(
            request.notebook_id,
            request.artifact_type,
            profile=request.profile,
            source_ids=request.source_ids,
            focus_prompt=request.focus_prompt,
            description=request.description,
            language=request.language,
        )
        return NotebookLMArtifactCreateResponse(**payload)
    except Exception as exc:
        _raise_notebooklm_http(exc)
