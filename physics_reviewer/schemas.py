from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class PaperInput(BaseModel):
    title: str | None = None
    paper_text: str = Field(min_length=1)


class ScoreCard(BaseModel):
    novelty: int = Field(ge=1, le=5)
    physics_correctness: int = Field(ge=1, le=5)
    method_rigor: int = Field(ge=1, le=5)
    reproducibility: int = Field(ge=1, le=5)
    citation_quality: int = Field(ge=1, le=5)
    writing_quality: int = Field(ge=1, le=5)
    overall_score: int = Field(ge=1, le=100)


class ReviewReport(BaseModel):
    title: str | None = None
    scores: ScoreCard
    strengths: list[str]
    weaknesses: list[str]
    required_checks: list[str]
    uncertainty_notes: list[str]
    summary: str


class AgentFinding(BaseModel):
    agent: str
    status: Literal["ok", "warning", "error"]
    findings: list[str]
    evidence: list[str] = Field(default_factory=list)


class ReviewResponse(BaseModel):
    report: ReviewReport
    findings: list[AgentFinding]
    literature: list["LiteraturePaper"] = Field(default_factory=list)


ReviewTaskStatus = Literal["queued", "running", "succeeded", "failed"]


class ReviewTaskCreated(BaseModel):
    task_id: str
    batch_id: str | None = None
    status: ReviewTaskStatus
    filename: str | None = None


class BatchCreated(BaseModel):
    batch_id: str
    tasks: list[ReviewTaskCreated]


class ReviewTaskStatusResponse(BaseModel):
    task_id: str
    batch_id: str | None = None
    filename: str | None = None
    title: str | None = None
    status: ReviewTaskStatus
    error: str | None = None
    result: ReviewResponse | None = None
    created_at: str
    updated_at: str


class BatchStatusResponse(BaseModel):
    batch_id: str
    total: int
    queued: int
    running: int
    succeeded: int
    failed: int
    tasks: list[ReviewTaskStatusResponse]


class ReviewState(TypedDict, total=False):
    review_id: str
    title: str | None
    paper_text: str
    sections: dict[str, str]
    rule_signals: dict[str, int | bool]
    selected_agents: list[str]
    findings: list[dict]
    scores: dict
    report: dict
    literature: list[dict]


class LiteraturePaper(BaseModel):
    source: str
    title: str
    abstract: str | None = None
    year: int | None = None
    url: str | None = None
    authors: list[str] = Field(default_factory=list)
    citation_count: int | None = None
    external_id: str | None = None

