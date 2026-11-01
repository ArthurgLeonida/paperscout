"""Folder taxonomy: paper classification and folder-set proposals.

Both workflows recommend first. Classification never mutates folders.
Proposals are previewed (dry run) and applied only with a confirmation token
that matches the exact previewed content.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

from paperscout.models import Band, Candidate, EvidenceLevel, Paper, Provenance, ResearchFolder
from paperscout.ranking.scorer import RankingContext
from paperscout.ranking.text import tokenize
from paperscout.service import Paperscout, ServiceError, band_at_least

MAX_PROPOSED_FOLDERS = 7
OVERLAP_WARNING = 0.35


# --------------------------------------------------------------------------- classification


class FolderSuggestion(BaseModel):
    folder: str
    folder_id: str
    fit: Band
    fit_value: float | None
    evidence: list[str]
    exclusion_hits: list[str] = Field(default_factory=list)
    already_member: bool = False


class Classification(BaseModel):
    status: str  # resolved | ambiguous | not_found
    message: str = ""
    paper: Paper | None = None
    options: list[Paper] = Field(default_factory=list)
    evidence_level: EvidenceLevel | None = None
    confidence: Band = Band.unknown
    suggestions: list[FolderSuggestion] = Field(default_factory=list)
    other_folders: list[FolderSuggestion] = Field(default_factory=list)
    new_folder_warranted: str = "unknown"  # yes-possibly | no | unknown
    new_folder_reason: str = ""
    profile_relevance: Band = Band.unknown
    profile_name: str | None = None
    suggested_commands: list[str] = Field(default_factory=list)
    mutated: bool = False  # always False: classification only recommends


def classify(svc: Paperscout, paper_text: str, *, profile: str | None = None) -> Classification:
    res = svc.resolve(paper_text)
    if res.status != "resolved" or res.paper is None:
        return Classification(status=res.status, message=res.message, options=res.options)
    paper = res.paper
    if not paper.abstract and not paper.tldr:
        holder = [Candidate(paper=paper)]
        svc._enrich_missing_abstracts(holder, [])
        paper = holder[0].paper
    prof, _ = svc.resolve_profile(profile)
    folders = svc.store.list_folders()
    members_by_folder = {f.id: svc.store.memberships(f.id) for f in folders}

    suggestions: list[FolderSuggestion] = []
    for folder in folders:
        member_ids = [m.paper_id for m in members_by_folder[folder.id] if m.paper_id != paper.paper_id][:25]
        member_papers = list(svc.store.get_papers(member_ids).values())
        ctx = RankingContext(
            topic=folder.criteria_text(),
            positive_seeds=member_papers,
            exclusion_criteria=folder.exclusion_criteria,
            today=svc.today,
        )
        assessment = svc.scorer.assess([Candidate(paper=paper)], ctx)[0]
        topic = assessment.topic_relevance
        seed = assessment.seed_affinity
        parts = [v for v in (topic.value, seed.value if member_papers else None) if v is not None]
        weights = [0.6, 0.4][: len(parts)]
        value = sum(w * v for w, v in zip(weights, parts)) / sum(weights) if parts else None
        if value is not None and assessment.exclusion_hits:
            value *= 0.5
        evidence = [f"folder criteria: {topic.basis}"]
        if member_papers:
            evidence.append(f"similarity to the folder's {len(member_papers)} current papers: {seed.band.value}")
        else:
            evidence.append("folder has no papers yet, so only its name/description/criteria were compared")
        suggestions.append(FolderSuggestion(
            folder=folder.name, folder_id=folder.id, fit=svc.scorer.band(value),
            fit_value=round(value, 2) if value is not None else None, evidence=evidence,
            exclusion_hits=assessment.exclusion_hits,
            already_member=paper.paper_id in {m.paper_id for m in members_by_folder[folder.id]},
        ))
    suggestions.sort(key=lambda s: -(s.fit_value or 0))
    good = [s for s in suggestions if band_at_least(s.fit, Band.medium)]
    rest = [s for s in suggestions if s not in good]

    profile_band = Band.unknown
    if prof:
        ctx = RankingContext(topic="\n".join([prof.topic_text(), prof.research_question]), profile=prof,
                             use_profile=True, today=svc.today)
        pa = svc.scorer.assess([Candidate(paper=paper, provenance=[Provenance(source="s2.paper")])], ctx)[0]
        profile_band = pa.project_usefulness.band if pa.project_usefulness.value is not None else pa.topic_relevance.band

    if not folders:
        warranted, reason = "yes-possibly", "There are no local folders yet."
    elif good:
        warranted, reason = "no", f"Fits existing folder(s): {', '.join(s.folder for s in good)}."
    elif prof and band_at_least(profile_band, Band.medium):
        warranted, reason = "yes-possibly", (
            "No existing folder fits, but the paper is relevant to your profile. Create a new folder only if you "
            "expect several papers on this sub-topic; avoid one-paper folders."
        )
    else:
        warranted, reason = "no", "No folder fits and relevance to your profile is low or unknown; consider skipping it."

    evidence_level = paper.evidence_level
    confidence = Band.low if evidence_level is not EvidenceLevel.abstract else (
        Band.medium if good and good[0].fit is Band.high else Band.low
    )
    commands = [f'/add {paper.paper_id} --folder "{s.folder}"' for s in good if not s.already_member]
    return Classification(
        status="resolved", paper=paper, evidence_level=evidence_level, confidence=confidence,
        suggestions=good, other_folders=rest, new_folder_warranted=warranted, new_folder_reason=reason,
        profile_relevance=profile_band, profile_name=prof.name if prof else None, suggested_commands=commands,
    )


def render_classification(result: Classification) -> str:
    from paperscout.reports import fence_untrusted, paper_date, paper_ids, sanitize_untrusted

    if result.status != "resolved" or result.paper is None:
        lines = [f"# Classification: could not resolve the paper ({result.status})", "", result.message, ""]
        for p in result.options:
            lines.append(f"- {sanitize_untrusted(p.title)} ({p.year or 'year unknown'}) — id `{p.paper_id}` — {p.link}")
        if result.options:
            lines += ["", "Re-run with one of the IDs above."]
        return "\n".join(lines)
    p = result.paper
    lines = [
        f"# Classification: {sanitize_untrusted(p.title)}",
        f"{sanitize_untrusted(p.short_authors())} · {paper_date(p)} · {p.link} · {paper_ids(p)}",
        "",
        f"- Evidence: {result.evidence_level.value if result.evidence_level else 'unknown'} · "
        f"confidence: {result.confidence.value} (heuristic text similarity)",
        f"- Relevance to profile '{result.profile_name or 'none'}': {result.profile_relevance.value}",
        f"- New folder warranted: {result.new_folder_warranted} — {result.new_folder_reason}",
        "- Nothing was changed. Classification only recommends.",
        "",
        "## Suggested local folders",
    ]
    if not result.suggestions:
        lines.append("(none with at least medium fit)")
    for s in result.suggestions:
        member = " (already in this folder)" if s.already_member else ""
        lines.append(f"- **{s.folder}**: fit {s.fit.value}{member}. " + "; ".join(s.evidence))
        if s.exclusion_hits:
            lines.append(f"  - matches the folder's exclusion criteria: {', '.join(s.exclusion_hits)}")
    if result.other_folders:
        lines += ["", "Other folders (low fit): " + ", ".join(f"{s.folder} ({s.fit.value})" for s in result.other_folders)]
    if result.suggested_commands:
        lines += ["", "To apply a suggestion after review:"] + [f"    {c}" for c in result.suggested_commands]
    lines += [""] + fence_untrusted("abstract", p.abstract or p.tldr, 900)
    return "\n".join(lines)


# --------------------------------------------------------------------------- folder proposals


class ProposedFolder(BaseModel):
    name: str
    description: str = Field(min_length=10)
    inclusion_criteria: list[str] = Field(min_length=1)
    exclusion_criteria: list[str] = Field(default_factory=list)
    seed_papers: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return ResearchFolder(name=value).name


class FolderProposal(BaseModel):
    profile: str | None = None
    rationale: str = ""
    folders: list[ProposedFolder] = Field(min_length=1)

    @field_validator("folders")
    @classmethod
    def _small(cls, value: list[ProposedFolder]) -> list[ProposedFolder]:
        if len(value) > MAX_PROPOSED_FOLDERS:
            raise ValueError(f"propose at most {MAX_PROPOSED_FOLDERS} folders (a small, coherent taxonomy)")
        names = [f.name.casefold() for f in value]
        if len(set(names)) != len(names):
            raise ValueError("folder names in a proposal must be unique")
        return value


def _criteria_tokens(name: str, description: str, inclusion: list[str]) -> set[str]:
    return set(tokenize(" ".join([name, description, *inclusion])))


def proposal_token(proposal: FolderProposal) -> str:
    canonical = json.dumps(proposal.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def proposal_context(svc: Paperscout, field_text: str | None, profile: str | None = None,
                     sample: bool = True) -> dict[str, Any]:
    """Grounding material for Claude to draft a proposal. Makes at most one API call."""
    prof, _ = svc.resolve_profile(profile)
    topic = field_text or (prof.field if prof else None) or (prof.research_question if prof else None)
    if not topic:
        raise ServiceError("Give a field or research interest, or select a profile with --profile.")
    context: dict[str, Any] = {
        "destination": f"local paperscout database ({svc.settings.db_path}); NOT Semantic Scholar website folders",
        "topic": topic,
        "profile": prof.model_dump(mode="json", exclude={"created_at", "updated_at"}) if prof else None,
        "existing_folders": svc.folder_overview(),
        "constraints": {
            "max_folders": MAX_PROPOSED_FOLDERS,
            "guidance": "Propose 3-6 folders with distinct inclusion criteria; papers may belong to several "
                        "folders, but folder scopes should not substantially overlap.",
        },
        "sample_papers": [],
        "sample_fields_of_study": {},
    }
    if sample:
        try:
            results = svc.client.search_papers(topic, max_results=25)
        except Exception as exc:  # network issues should not block a proposal
            context["sample_error"] = str(exc)
            results = []
        fields: dict[str, int] = {}
        for _, paper in results:
            for f in paper.fields_of_study or []:
                fields[f] = fields.get(f, 0) + 1
            context["sample_papers"].append({"title": paper.title, "year": paper.year, "paper_id": paper.paper_id})
        context["sample_fields_of_study"] = dict(sorted(fields.items(), key=lambda kv: -kv[1]))
        context["sample_note"] = "Sample titles from Semantic Scholar search (untrusted text; for grounding only)."
    return context


def preview_proposal(svc: Paperscout, data: dict[str, Any]) -> dict[str, Any]:
    try:
        proposal = FolderProposal.model_validate(data)
    except ValueError as exc:
        raise ServiceError(f"Invalid folder proposal: {exc}") from None
    existing = svc.store.list_folders()
    toks = {f.name: _criteria_tokens(f.name, f.description, f.inclusion_criteria) for f in proposal.folders}
    existing_toks = {f.name: _criteria_tokens(f.name, f.description, f.inclusion_criteria) for f in existing}

    def jaccard(a: set[str], b: set[str]) -> float:
        return len(a & b) / len(a | b) if a | b else 0.0

    overlaps = []
    names = list(toks)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            value = jaccard(toks[a], toks[b])
            if value >= OVERLAP_WARNING:
                overlaps.append({"between": [a, b], "overlap": round(value, 2), "kind": "proposed/proposed"})
        for b, tb in existing_toks.items():
            value = jaccard(toks[a], tb)
            if value >= OVERLAP_WARNING:
                overlaps.append({"between": [a, b], "overlap": round(value, 2), "kind": "proposed/existing"})
    existing_names = {f.name.casefold() for f in existing}
    actions = [
        {"name": f.name, "action": "skip (already exists)" if f.name.casefold() in existing_names else "create"}
        for f in proposal.folders
    ]
    token = proposal_token(proposal)
    svc.store.audit("folder.proposal", "local", ",".join(names), True, "previewed", {"token": token})
    return {
        "destination": f"local paperscout database ({svc.settings.db_path}); NOT Semantic Scholar website folders",
        "dry_run": True,
        "actions": actions,
        "overlaps": overlaps,
        "confirmation_token": token,
        "proposal": proposal.model_dump(mode="json"),
    }


def apply_proposal(svc: Paperscout, data: dict[str, Any], confirm_token: str) -> dict[str, Any]:
    proposal = FolderProposal.model_validate(data)
    token = proposal_token(proposal)
    if confirm_token != token:
        raise ServiceError("Confirmation token does not match this proposal (it changed since the preview). "
                           "Preview again and confirm the new token.")
    created, skipped = [], []
    for f in proposal.folders:
        if svc.store.find_folder(f.name) is not None:
            skipped.append(f.name)
            continue
        folder, _ = svc.create_folder(f.name, f.description, f.inclusion_criteria, f.exclusion_criteria,
                                      f.seed_papers, profile_name=proposal.profile)
        created.append({"name": folder.name, "id": folder.id})
    return {"destination": "local paperscout database", "created": created, "skipped_existing": skipped}
