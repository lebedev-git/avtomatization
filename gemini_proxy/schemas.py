from __future__ import annotations

from datetime import date as DateValue
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class InputFile(BaseModel):
    path: str | None = None
    base64_data: str | None = None
    url: str | None = None
    filename: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "InputFile":
        provided = [self.path, self.base64_data, self.url]
        if sum(value is not None and value != "" for value in provided) != 1:
            raise ValueError(
                "Exactly one of path, base64_data, or url must be provided."
            )
        return self


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: str | None = None
    gem: str | None = None
    temporary: bool = False
    timeout_sec: float | None = Field(default=None, ge=1, le=1200)
    watchdog_timeout_sec: float | None = Field(default=None, ge=1, le=300)
    disable_internal_retry: bool = False
    chat_metadata: list[str] | None = None
    files: list[InputFile] = Field(default_factory=list)
    save_images: bool = False
    include_image_base64: bool = False
    image_output_subdir: str | None = None
    image_filename_prefix: str | None = None


class AuthCheckResponse(BaseModel):
    ok: bool
    initialized: bool
    auth_sources_hint: list[str]
    cookie_json_path: str | None = None
    active_cookie_names: list[str] = Field(default_factory=list)
    build_label: str | None = None
    session_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    initialized: bool
    downloads_dir: str
    auth_sources_hint: list[str]


class ImagePayload(BaseModel):
    kind: str
    title: str
    alt: str
    url: str
    saved_path: str | None = None
    base64_data: str | None = None


class CandidatePayload(BaseModel):
    rcid: str
    text: str
    thoughts: str | None = None
    images: list[ImagePayload] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    metadata: list[str]
    chosen: int
    text: str
    thoughts: str | None = None
    images: list[ImagePayload] = Field(default_factory=list)
    candidates: list[CandidatePayload] = Field(default_factory=list)
    raw: dict[str, Any] | None = None


class WebGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    image_tool: bool = False
    headless: bool = True
    file_path: str | None = None
    timeout_sec: int = Field(default=90, ge=10, le=1200)
    wait_after_submit_sec: int = Field(default=20, ge=5, le=180)
    capture_label: str | None = None


class WebImagePayload(BaseModel):
    src: str
    alt: str | None = None
    saved_path: str | None = None
    saved_url: str | None = None


class WebLoginRequest(BaseModel):
    timeout_sec: int = Field(default=900, ge=30, le=3600)
    headless: bool = False


class WebLoginResponse(BaseModel):
    ok: bool
    signed_in: bool
    already_signed_in: bool = False
    profile_dir: str
    message: str
    current_url: str | None = None


class WebGenerateResponse(BaseModel):
    ok: bool
    mode: Literal["pro"] = "pro"
    mode_actual: str | None = None
    image_tool_requested: bool
    image_tool_active: bool = False
    prompt: str
    assistant_text: str | None = None
    thought_text: str | None = None
    last_turn_text: str | None = None
    capture_dir: str | None = None
    before_capture_path: str | None = None
    after_capture_path: str | None = None
    before_capture_url: str | None = None
    after_capture_url: str | None = None
    stream_response_path: str | None = None
    stream_response_url: str | None = None
    stream_request_summary: dict[str, Any] | None = None
    stream_response_excerpt: str | None = None
    images: list[WebImagePayload] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class NotebookLMProfile(BaseModel):
    name: str
    exists: bool = False
    profile_dir: str
    email: str | None = None
    last_validated: str | None = None


class NotebookLMAuthStatusResponse(BaseModel):
    ok: bool
    authenticated: bool
    profile: str
    profile_dir: str
    email: str | None = None
    notebook_count: int = 0
    message: str
    profiles: list[NotebookLMProfile] = Field(default_factory=list)


class NotebookLMLoginRequest(BaseModel):
    profile: str = "default"
    timeout_sec: int = Field(default=300, ge=30, le=3600)


class NotebookLMNotebook(BaseModel):
    id: str
    title: str
    source_count: int = 0
    sources: list[dict[str, Any]] = Field(default_factory=list)
    is_owned: bool = True
    is_shared: bool = False
    created_at: str | None = None
    modified_at: str | None = None
    url: str | None = None
    ownership: str | None = None


class NotebookLMNotebookListResponse(BaseModel):
    profile: str
    notebooks: list[NotebookLMNotebook] = Field(default_factory=list)


class NotebookLMCreateNotebookRequest(BaseModel):
    title: str = Field(min_length=1)
    profile: str = "default"


class NotebookLMCreateNotebookResponse(BaseModel):
    ok: bool
    profile: str
    notebook: NotebookLMNotebook


class NotebookLMSource(BaseModel):
    id: str | None = None
    title: str | None = None
    type: str | None = None
    url: str | None = None
    is_stale: bool | None = None
    original_type: str | None = None


class NotebookLMSourceListResponse(BaseModel):
    profile: str
    notebook_id: str
    sources: list[NotebookLMSource] = Field(default_factory=list)


class NotebookLMAddSourceRequest(BaseModel):
    profile: str = "default"
    notebook_id: str = Field(min_length=1)
    kind: Literal["auto", "url", "text", "drive", "youtube"] = "auto"
    value: str = Field(min_length=1)
    title: str | None = None
    doc_type: Literal["doc", "slides", "sheets", "pdf"] = "doc"
    wait_timeout_sec: int = Field(default=30, ge=3, le=300)


class NotebookLMAddSourceResponse(BaseModel):
    ok: bool
    profile: str
    notebook_id: str
    requested_kind: str
    resolved_kind: str
    value: str
    title: str | None = None
    doc_type: str | None = None
    new_sources: list[NotebookLMSource] = Field(default_factory=list)
    sources: list[NotebookLMSource] = Field(default_factory=list)
    raw: Any | None = None


class NotebookLMQueryRequest(BaseModel):
    profile: str = "default"
    notebook_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)
    conversation_id: str | None = None


class NotebookLMQueryResponse(BaseModel):
    ok: bool
    profile: str
    notebook_id: str
    prompt: str
    answer: str
    conversation_id: str | None = None
    turn_number: int | None = None
    is_follow_up: bool | None = None
    sources: list[NotebookLMSource] = Field(default_factory=list)
    citations: dict[str, str] = Field(default_factory=dict)


class NotebookLMArtifactCreateRequest(BaseModel):
    profile: str = "default"
    notebook_id: str = Field(min_length=1)
    artifact_type: Literal[
        "audio",
        "report",
        "quiz",
        "flashcards",
        "mindmap",
        "slides",
        "infographic",
        "video",
        "data-table",
    ]
    source_ids: list[str] = Field(default_factory=list)
    focus_prompt: str | None = None
    description: str | None = None
    language: str | None = None


class NotebookLMStudioStatusResponse(BaseModel):
    profile: str
    notebook_id: str
    items: list[dict[str, Any]] = Field(default_factory=list)


class NotebookLMArtifactCreateResponse(BaseModel):
    ok: bool
    profile: str
    notebook_id: str
    artifact_type: str
    raw: Any | None = None
    studio_items: list[dict[str, Any]] = Field(default_factory=list)


class AgentSummary(BaseModel):
    id: str
    name: str
    description: str
    kind: Literal["text-report", "media-report"]


class AgentListResponse(BaseModel):
    agents: list[AgentSummary] = Field(default_factory=list)


class ProtocolAgentConfig(BaseModel):
    agent_id: str = "protocol"
    name: str = "Протокол"
    description: str = "Принимает запись встречи и формирует итоговый протокол."
    analysis_prompt: str = Field(min_length=1)
    protocol_prompt: str = Field(min_length=1)
    updated_at: str | None = None


class UpdateProtocolAgentConfigRequest(BaseModel):
    analysis_prompt: str = Field(min_length=1)
    protocol_prompt: str = Field(min_length=1)


class AnalyticsSourceForm(BaseModel):
    id: str
    name: str
    url: str
    survey_id: str


class AnalyticsBlockConfig(BaseModel):
    id: Literal["day1", "day2", "summary", "infographic"]
    name: str
    description: str
    mode: Literal["single-date", "date-range", "reports-only", "notebooklm-infographic"]
    system_prompt: str = Field(min_length=1)
    source_forms: list[AnalyticsSourceForm] = Field(default_factory=list)


class AnalyticsAgentConfig(BaseModel):
    agent_id: str = "analytics-note"
    name: str = "Аналитическая записка"
    description: str = (
        "Собирает отдельные аналитические записки по первому и второму дню, "
        "а затем формирует общую итоговую аналитику."
    )
    blocks: list[AnalyticsBlockConfig] = Field(default_factory=list)
    synced_to_n8n: bool = False
    sync_message: str | None = None
    updated_at: str | None = None


class UpdateAnalyticsAgentConfigRequest(BaseModel):
    day1_prompt: str = Field(min_length=1)
    day2_prompt: str = Field(min_length=1)
    summary_prompt: str = Field(min_length=1)
    infographic_prompt: str = Field(min_length=1)


class AnalyticsDateOption(BaseModel):
    date: str
    label: str
    count: int
    secondary_count: int | None = None
    total_count: int | None = None


class AnalyticsDay1HistoryResponse(BaseModel):
    block_id: Literal["day1"] = "day1"
    total_entry_answers: int
    total_exit_answers: int
    available_dates: list[AnalyticsDateOption] = Field(default_factory=list)
    default_date: str | None = None
    selected_date: str | None = None
    locked: bool = False
    payload_source: Literal["remote", "cache"]
    payload_message: str | None = None
    source_forms: list[AnalyticsSourceForm] = Field(default_factory=list)
    timeline: ProcessingTimeline | None = None


class AnalyticsDay2HistoryResponse(BaseModel):
    block_id: Literal["day2"] = "day2"
    total_answers: int
    available_dates: list[AnalyticsDateOption] = Field(default_factory=list)
    date_from_default: str | None = None
    date_to_default: str | None = None
    selected_date_from: str | None = None
    selected_date_to: str | None = None
    locked: bool = False
    payload_source: Literal["remote", "cache"]
    payload_message: str | None = None
    source_forms: list[AnalyticsSourceForm] = Field(default_factory=list)
    timeline: ProcessingTimeline | None = None


class AnalyticsDay1RunRequest(BaseModel):
    date: DateValue | None = None


class AnalyticsDay2RunRequest(BaseModel):
    date_from: DateValue | None = None
    date_to: DateValue | None = None


class AnalyticsSummaryRunRequest(BaseModel):
    pass


class AnalyticsInfographicRunRequest(BaseModel):
    google_doc_url: str = Field(min_length=1)


class ProcessingTimelineStep(BaseModel):
    id: str
    label: str
    status: Literal["pending", "active", "completed", "error"] = "pending"
    message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ProcessingTimeline(BaseModel):
    running: bool = False
    current_step_id: str | None = None
    summary: str | None = None
    updated_at: str | None = None
    steps: list[ProcessingTimelineStep] = Field(default_factory=list)


class AnalyticsRunResultBase(BaseModel):
    ok: bool
    block_id: Literal["day1", "day2", "summary"]
    block_name: str
    payload_source: Literal["remote", "cache"] | None = None
    payload_message: str | None = None
    generation_method: Literal["direct", "web"] = "direct"
    generation_message: str | None = None
    report_text: str
    document_name: str
    document_path: str
    document_url: str
    n8n_roundtrip_ok: bool = False
    n8n_roundtrip_message: str | None = None
    created_at: str | None = None
    title: str | None = None
    timeline: ProcessingTimeline | None = None


class AnalyticsDay1RunResponse(AnalyticsRunResultBase):
    block_id: Literal["day1"] = "day1"
    selected_date: str
    entry_answers: int
    exit_answers: int


class AnalyticsDay2RunResponse(AnalyticsRunResultBase):
    block_id: Literal["day2"] = "day2"
    date_from: str
    date_to: str | None = None
    filter_mode: Literal["single-day", "range"]
    total_answers: int
    filtered_answers: int


class AnalyticsReportState(BaseModel):
    block_id: Literal["day1", "day2", "summary"]
    block_name: str
    ready: bool
    stale: bool = False
    title: str | None = None
    created_at: str | None = None
    period_label: str | None = None
    document_name: str | None = None
    document_url: str | None = None
    selected_date: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    timeline: ProcessingTimeline | None = None


class AnalyticsInfographicState(BaseModel):
    block_id: Literal["infographic"] = "infographic"
    block_name: str
    ready: bool
    stale: bool = False
    created_at: str | None = None
    notebook_id: str | None = None
    notebook_title: str | None = None
    notebook_url: str | None = None
    profile: str | None = None
    google_doc_url: str | None = None
    summary_created_at: str | None = None
    summary_title: str | None = None
    photo_name: str | None = None
    photo_url: str | None = None
    logo_name: str | None = None
    logo_url: str | None = None
    image_name: str | None = None
    image_url: str | None = None
    studio_items: list[dict[str, Any]] = Field(default_factory=list)
    timeline: ProcessingTimeline | None = None


class AnalyticsSummaryStateResponse(BaseModel):
    block_id: Literal["summary"] = "summary"
    ready: bool
    dependencies_ready: bool
    infographic_dependencies_ready: bool
    day1: AnalyticsReportState
    day2: AnalyticsReportState
    summary: AnalyticsReportState
    infographic: AnalyticsInfographicState


class AnalyticsSummaryRunResponse(AnalyticsRunResultBase):
    block_id: Literal["summary"] = "summary"
    day1_report_created_at: str
    day2_report_created_at: str
    day1_period_label: str | None = None
    day2_period_label: str | None = None


class AnalyticsInfographicRunResponse(BaseModel):
    ok: bool
    block_id: Literal["infographic"] = "infographic"
    block_name: str
    created_at: str
    notebook_id: str
    notebook_title: str | None = None
    notebook_url: str | None = None
    profile: str
    google_doc_url: str
    photo_name: str
    photo_url: str
    logo_name: str
    logo_url: str
    image_name: str
    image_url: str
    photo_description: str
    logo_description: str
    summary_created_at: str
    summary_title: str | None = None
    studio_items: list[dict[str, Any]] = Field(default_factory=list)
    timeline: ProcessingTimeline | None = None


class AnalyticsResetResponse(BaseModel):
    ok: bool
    cleared_blocks: list[Literal["day1", "day2", "summary", "infographic"]] = Field(default_factory=list)
    message: str


class AnalyticsSourceStatusItem(BaseModel):
    block_id: Literal["day1", "day2"]
    block_name: str
    ok: bool
    payload_source: Literal["remote", "cache"] | None = None
    total_answers: int
    message: str | None = None


class AnalyticsSourceStatusResponse(BaseModel):
    ok: bool
    statuses: list[AnalyticsSourceStatusItem] = Field(default_factory=list)


class ProtocolReportState(BaseModel):
    agent_id: Literal["protocol"] = "protocol"
    ready: bool
    title: str | None = None
    created_at: str | None = None
    source_name: str | None = None
    source_mime_type: str | None = None
    processing_strategy: str | None = None
    preprocessing_message: str | None = None
    document_name: str | None = None
    document_url: str | None = None
    transcript_name: str | None = None
    transcript_url: str | None = None
    chunk_count: int | None = None
    duration_seconds: float | None = None
    timeline: ProcessingTimeline | None = None


class ProtocolRunResponse(BaseModel):
    ok: bool
    agent_id: Literal["protocol"] = "protocol"
    agent_name: str
    source_name: str
    source_mime_type: str
    processing_strategy: str
    preprocessing_message: str | None = None
    generation_method: Literal["direct"] = "direct"
    generation_message: str | None = None
    report_text: str
    document_name: str
    document_path: str
    document_url: str
    transcript_name: str
    transcript_path: str
    transcript_url: str
    n8n_roundtrip_ok: bool = False
    n8n_roundtrip_message: str | None = None
    created_at: str | None = None
    title: str | None = None
    chunk_count: int | None = None
    duration_seconds: float | None = None
    timeline: ProcessingTimeline | None = None


class ProtocolResetResponse(BaseModel):
    ok: bool
    cleared: bool
    message: str
