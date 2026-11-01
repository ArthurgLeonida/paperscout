"""Typed domain models.

Unknown values are represented as ``None`` (rendered as "unknown"), never as
``False`` or ``0``.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceLevel(str, Enum):
    """What the assessment could see. Ordered from weakest to strongest."""

    metadata = "metadata"  # title, venue, authors, dates only
    tldr = "tldr"  # metadata + Semantic Scholar's machine-generated TLDR
    abstract = "abstract"  # metadata + author-written abstract
    full_text = "full_text"  # full text obtained through permitted means

    @property
    def rank(self) -> int:
        return list(EvidenceLevel).index(self)


class ReadingRole(str, Enum):
    foundational = "foundational"
    prerequisite = "prerequisite"
    survey = "survey"
    recent_method = "recent_method"
    method = "method"
    benchmark_dataset = "benchmark_dataset"
    unknown = "unknown"


class ReadingDepth(str, Enum):
    skip = "skip"
    overview = "overview"
    substantive = "substantive"
    deep = "deep"


class Band(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class Priority(str, Enum):
    p1 = "P1"
    p2 = "P2"
    p3 = "P3"
    unknown = "unknown"  # nothing to judge relevance against (no topic, criteria, or profile)
    skip = "skip"


class FeedbackLabel(str, Enum):
    relevant = "relevant"
    irrelevant = "irrelevant"
    already_known = "already_known"
    read_later = "read_later"
    read_overview = "read_overview"
    understood = "understood"
    deep_study = "deep_study"
    implemented = "implemented"
    not_useful = "not_useful"

    @property
    def counts_as_read(self) -> bool:
        return self in READ_LABELS

    @property
    def counts_as_reviewed(self) -> bool:
        return self is not FeedbackLabel.read_later

    @property
    def negative(self) -> bool:
        return self in (FeedbackLabel.irrelevant, FeedbackLabel.not_useful)


READ_LABELS = {
    FeedbackLabel.already_known,
    FeedbackLabel.read_overview,
    FeedbackLabel.understood,
    FeedbackLabel.deep_study,
    FeedbackLabel.implemented,
}

FEEDBACK_ALIASES = {
    "known": FeedbackLabel.already_known,
    "already-known": FeedbackLabel.already_known,
    "read-later": FeedbackLabel.read_later,
    "later": FeedbackLabel.read_later,
    "overview": FeedbackLabel.read_overview,
    "read-overview": FeedbackLabel.read_overview,
    "deep": FeedbackLabel.deep_study,
    "deep-study": FeedbackLabel.deep_study,
    "not-useful": FeedbackLabel.not_useful,
    "not_useful_for_project": FeedbackLabel.not_useful,
}


def parse_feedback_label(text: str) -> FeedbackLabel:
    key = text.strip().lower()
    if key in FEEDBACK_ALIASES:
        return FEEDBACK_ALIASES[key]
    try:
        return FeedbackLabel(key.replace("-", "_"))
    except ValueError:
        valid = ", ".join(label.value for label in FeedbackLabel)
        raise ValueError(f"Unknown feedback label {text!r}. Valid labels: {valid}") from None


# --------------------------------------------------------------------------- papers


class Author(BaseModel):
    name: str
    author_id: str | None = None


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Paper(BaseModel):
    """A metadata snapshot of one paper, as returned by a provider."""

    paper_id: str
    title: str
    corpus_id: int | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    abstract: str | None = None
    tldr: str | None = None
    venue: str | None = None
    year: int | None = None
    publication_date: str | None = None  # ISO YYYY-MM-DD when the provider knows the day
    publication_types: list[str] | None = None
    fields_of_study: list[str] | None = None
    citation_count: int | None = None
    influential_citation_count: int | None = None
    reference_count: int | None = None
    is_open_access: bool | None = None
    open_access_pdf_url: str | None = None
    authors: list[Author] = Field(default_factory=list)

    @field_validator("publication_date")
    @classmethod
    def _valid_date(cls, value: str | None) -> str | None:
        if value and not _DATE_RE.match(value):
            return None
        return value

    @property
    def doi(self) -> str | None:
        value = self.external_ids.get("DOI")
        return value.lower() if value else None

    @property
    def arxiv_id(self) -> str | None:
        return self.external_ids.get("ArXiv")

    @property
    def link(self) -> str:
        return self.url or f"https://www.semanticscholar.org/paper/{self.paper_id}"

    @property
    def evidence_level(self) -> EvidenceLevel:
        if self.abstract:
            return EvidenceLevel.abstract
        if self.tldr:
            return EvidenceLevel.tldr
        return EvidenceLevel.metadata

    @property
    def date_precision(self) -> str:
        if self.publication_date:
            return "day"
        if self.year:
            return "year"
        return "unknown"

    def published_on(self) -> date | None:
        return date.fromisoformat(self.publication_date) if self.publication_date else None

    def short_authors(self, limit: int = 3) -> str:
        names = [a.name for a in self.authors]
        if not names:
            return "unknown authors"
        return ", ".join(names[:limit]) + (" et al." if len(names) > limit else "")

    @classmethod
    def from_s2(cls, data: dict[str, Any]) -> "Paper":
        tldr = data.get("tldr") or {}
        oa = data.get("openAccessPdf") or {}
        external = {k: str(v) for k, v in (data.get("externalIds") or {}).items() if v is not None}
        fos = data.get("fieldsOfStudy")
        if fos is None and data.get("s2FieldsOfStudy"):
            fos = sorted({f.get("category") for f in data["s2FieldsOfStudy"] if f.get("category")})
        return cls(
            paper_id=data["paperId"],
            title=(data.get("title") or "").strip() or "(untitled)",
            corpus_id=data.get("corpusId"),
            external_ids=external,
            url=data.get("url"),
            abstract=(data.get("abstract") or None),
            tldr=(tldr.get("text") if isinstance(tldr, dict) else None) or None,
            venue=(data.get("venue") or None),
            year=data.get("year"),
            publication_date=data.get("publicationDate"),
            publication_types=data.get("publicationTypes"),
            fields_of_study=fos,
            citation_count=data.get("citationCount"),
            influential_citation_count=data.get("influentialCitationCount"),
            reference_count=data.get("referenceCount"),
            is_open_access=data.get("isOpenAccess"),
            open_access_pdf_url=(oa.get("url") or None) if isinstance(oa, dict) else None,
            authors=[
                Author(name=a.get("name") or "unknown", author_id=a.get("authorId"))
                for a in (data.get("authors") or [])
            ],
        )


class Provenance(BaseModel):
    """How a candidate was obtained. A paper can carry several."""

    source: str  # e.g. s2.search, s2.recommendations, import:bibtex, local.folder
    query: str | None = None
    seed: str | None = None
    rank: int | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    detail: str | None = None


class Candidate(BaseModel):
    paper: Paper
    provenance: list[Provenance] = Field(default_factory=list)


# --------------------------------------------------------------------------- profiles & folders


class RecencyPreference(str, Enum):
    strong = "strong"
    moderate = "moderate"
    none = "none"


class ResearchProfile(BaseModel):
    id: str = ""
    name: str
    field: str = ""
    research_question: str = ""
    project_description: str = ""
    desired_outcomes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    positive_seeds: list[str] = Field(default_factory=list)
    negative_seeds: list[str] = Field(default_factory=list)
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    recency_preference: RecencyPreference = RecencyPreference.moderate
    include_foundational: bool = True
    languages: list[str] = Field(default_factory=lambda: ["en"])
    constraints: str = ""
    weekly_reading_hours: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("profile name must not be empty")
        return value

    @field_validator("weekly_reading_hours")
    @classmethod
    def _hours(cls, value: float | None) -> float | None:
        if value is not None and not (0 < value <= 80):
            raise ValueError("weekly_reading_hours must be between 0 and 80")
        return value

    def goal_text(self) -> str:
        parts = [self.research_question, self.project_description, *self.desired_outcomes]
        return "\n".join(p for p in parts if p)

    def topic_text(self) -> str:
        parts = [self.field, *self.keywords, *self.inclusion_criteria]
        return "\n".join(p for p in parts if p)


class SourceMapping(BaseModel):
    """Link between a local folder and an external collection (never implies sync)."""

    kind: str  # e.g. "zotero-collection", "s2-export-file"
    external_id: str | None = None
    label: str | None = None


class ResearchFolder(BaseModel):
    id: str = ""
    name: str
    description: str = ""
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    seed_papers: list[str] = Field(default_factory=list)
    profile_id: str | None = None
    source_mapping: SourceMapping | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_discovery_at: datetime | None = None
    last_ranked_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("folder name must not be empty")
        if len(value) > 120:
            raise ValueError("folder name must be at most 120 characters")
        return value

    def criteria_text(self) -> str:
        return "\n".join([self.name, self.description, *self.inclusion_criteria])


class FolderMembership(BaseModel):
    folder_id: str
    paper_id: str
    added_at: datetime
    added_by: str  # user | import | classification
    source: str  # local | import:bibtex | import:ris | import:ids ...
    note: str | None = None


class FeedbackEvent(BaseModel):
    id: int | None = None
    paper_id: str
    label: FeedbackLabel
    reason: str | None = None
    profile_id: str | None = None
    folder_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- assessments


class Component(BaseModel):
    """One separately reported ranking dimension. value=None means unknown."""

    value: float | None = None
    band: Band = Band.unknown
    basis: str = ""


class HeuristicAssessment(BaseModel):
    method: str  # ranking config version, e.g. heuristic-v1
    topic_relevance: Component
    project_usefulness: Component
    seed_affinity: Component
    recency: Component
    evidence_level: EvidenceLevel
    confidence: Band
    reading_role: ReadingRole
    role_basis: str
    priority: Priority
    heuristic_score: float | None  # NOT a probability; for ordering only
    reading_depth: ReadingDepth
    effort_minutes: tuple[int, int]  # planning heuristic range
    matched_terms: list[str] = Field(default_factory=list)
    exclusion_hits: list[str] = Field(default_factory=list)
    feedback_note: str | None = None
    uncertainties: list[str] = Field(default_factory=list)
    next_action: str = ""


class RankedPaper(BaseModel):
    paper: Paper
    provenance: list[Provenance] = Field(default_factory=list)
    assessment: HeuristicAssessment
    folders: list[str] = Field(default_factory=list)
    per_folder: dict[str, str] = Field(default_factory=dict)  # folder name -> priority
    latest_feedback: FeedbackLabel | None = None
    seen_before: bool = False


QUALITY_FIELDS = (
    "experimental_fairness",
    "derivation_correctness",
    "reproducibility",
    "code_availability",
    "state_of_the_art_claim",
)


class ClaudeAssessment(BaseModel):
    """An assessment written by Claude (in Claude Code) and validated before storage.

    Quality judgments must be "unknown" unless full text was actually used,
    and code availability additionally needs an independently verified source.
    """

    paper_id: str
    model: str
    prompt_version: str
    evidence_level: EvidenceLevel
    topic_relevance: Band
    project_usefulness: Band
    reading_role: ReadingRole
    priority: Priority
    reading_depth: ReadingDepth
    rationale: str = Field(min_length=10)
    evidence_quotes: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(min_length=1)
    next_action: str = Field(min_length=3)
    quality_judgments: dict[str, str] = Field(default_factory=dict)
    code_verified_source: str | None = None
    full_text_version: str | None = None

    @model_validator(mode="after")
    def _evidence_limits(self) -> "ClaudeAssessment":
        unknown_keys = set(self.quality_judgments) - set(QUALITY_FIELDS)
        if unknown_keys:
            raise ValueError(f"unknown quality_judgments keys: {sorted(unknown_keys)}")
        for key in QUALITY_FIELDS:
            self.quality_judgments.setdefault(key, "unknown")
        if self.evidence_level is not EvidenceLevel.full_text:
            judged = [k for k, v in self.quality_judgments.items() if v.strip().lower() != "unknown"]
            if judged:
                raise ValueError(
                    "quality judgments require full-text evidence; with "
                    f"{self.evidence_level.value} evidence these must be 'unknown': {sorted(judged)}"
                )
        elif not self.full_text_version:
            raise ValueError("full_text evidence requires full_text_version (document version/source)")
        if self.quality_judgments["code_availability"].lower() != "unknown" and not self.code_verified_source:
            raise ValueError("code_availability needs code_verified_source (independently verified)")
        return self
