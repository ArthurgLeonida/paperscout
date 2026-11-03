"""Application service layer.

The CLI, the Claude Code skills (which call the CLI) and the MCP server all go
through this class; there is exactly one implementation of retrieval,
ranking, and storage logic.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import httpx

from paperscout.background import PLACEHOLDER, file_hash
from paperscout.capabilities import check_rank_source, require
from paperscout.config import Settings, load_settings
from paperscout.dedup import deduplicate
from paperscout.identifiers import IdentifierError, PaperRef, normalize_title, parse_paper_ref
from paperscout.importers import ImportEntry, read_entries
from paperscout.models import (
    Band,
    Candidate,
    ClaudeAssessment,
    FeedbackEvent,
    FeedbackLabel,
    Paper,
    Priority,
    Provenance,
    RankedPaper,
    ReadingRole,
    RecencyPreference,
    ResearchFolder,
    ResearchProfile,
    parse_feedback_label,
    utcnow,
)
from paperscout.ranking.scorer import HeuristicScorer, RankingConfig, RankingContext, load_ranking_config
from paperscout.reports import ExcludedPaper, FolderSection, RankingReport
from paperscout.http import ApiError, ApiNotFound
from paperscout.arxiv.client import ArxivClient
from paperscout.s2.client import SemanticScholarClient
from paperscout.store.db import Conflict, NotFound, Store, connect

log = logging.getLogger("paperscout.service")

PRIORITY_ORDER = {Priority.p1: 0, Priority.p2: 1, Priority.p3: 2, Priority.unknown: 3, Priority.skip: 4}
S2_PAPER_ID = re.compile(r"^[0-9a-f]{40}$")
SOURCE_LABELS = {
    "semantic-scholar": "Semantic Scholar public API (search + recommendations), not your website Research Feed",
    "arxiv": "arXiv API (keyword search over arXiv preprints), not your Semantic Scholar feed",
}
DATE_BASIS = {
    "semantic-scholar": "Semantic Scholar publicationDate, often the first public version (e.g. arXiv v1); "
                        "indexing date is not used",
    "arxiv": "arXiv submission date of the first version (v1); later revisions are not used",
}
ARXIV_LIMITS = ("Source: arXiv (no Semantic Scholar key configured, or arXiv selected). arXiv provides no citation "
                "counts, recommendations or reference lists, so foundational status and shared prerequisites are "
                "not inferred, and only arXiv preprints are found.")


class ServiceError(RuntimeError):
    """A user-facing error with an actionable message."""


@dataclass
class Resolution:
    status: str  # resolved | ambiguous | not_found
    ref: PaperRef | None
    paper: Paper | None = None
    options: list[Paper] = field(default_factory=list)
    message: str = ""


def _title_similarity(a: str, b: str) -> float:
    ta, tb = set(normalize_title(a).split()), set(normalize_title(b).split())
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


class Paperscout:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        client: SemanticScholarClient,
        config: RankingConfig | None = None,
        today: date | None = None,
        arxiv: ArxivClient | None = None,
    ):
        self.settings = settings
        self.store = store
        self.client = client
        self.arxiv = arxiv or ArxivClient(settings, cache=store)
        self.provider = settings.effective_provider  # "arxiv" or "semantic-scholar"; the CLI may override it
        self.config = config or load_ranking_config()
        self.scorer = HeuristicScorer(self.config)
        self.today = today or date.today()

    @classmethod
    def open(cls, settings: Settings | None = None, transport: httpx.BaseTransport | None = None,
             today: date | None = None) -> "Paperscout":
        settings = settings or load_settings()
        store = Store(connect(settings.db_path))
        client = SemanticScholarClient(settings, cache=store, transport=transport)
        arxiv = ArxivClient(settings, cache=store, transport=transport)
        return cls(settings, store, client, today=today, arxiv=arxiv)

    def close(self) -> None:
        """Release clients and the database; safe to call more than once."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self.client.close()
        self.arxiv.close()
        self.store.cache_purge_expired()
        self.store.conn.close()

    # ================================================================== providers
    @property
    def uses_arxiv(self) -> bool:
        return self.provider == "arxiv"

    def api_stats(self) -> dict[str, int]:
        return {
            "api_calls": self.client.network_calls + self.arxiv.network_calls,
            "cache_hits": self.client.cache_hits + self.arxiv.cache_hits,
        }

    def search_sample(self, query: str, max_results: int = 25) -> list[tuple[int, Paper]]:
        """Plain search with the active provider (used to ground folder proposals)."""
        if self.uses_arxiv:
            return self.arxiv.search_papers(query, max_results=max_results)
        return self.client.search_papers(query, max_results=max_results)

    @staticmethod
    def _merge_metadata(local: Paper, new: Paper) -> Paper:
        """Keep the stored identity and fill gaps, so a paper seen through two providers stays one record."""
        data = new.model_dump()
        for key, value in local.model_dump().items():
            if data.get(key) in (None, "", [], {}):
                data[key] = value
        if (new.venue or "").startswith("arXiv preprint") and local.venue:
            data["venue"] = local.venue
        data["external_ids"] = {**local.external_ids, **new.external_ids}
        data["paper_id"] = local.paper_id
        return Paper.model_validate(data)

    def _canonicalize(self, candidates: list[Candidate]) -> None:
        """Map candidates to papers already stored under another provider's ID, so feedback and folders follow."""
        for cand in candidates:
            paper = cand.paper
            if self.store.get_paper(paper.paper_id) is not None:
                continue
            local = self.store.find_local_paper(doi=paper.doi, arxiv_id=paper.arxiv_id)
            if local is not None and local.paper_id != paper.paper_id:
                cand.paper = self._merge_metadata(local, paper)

    # ================================================================== profiles
    def save_profile(self, profile: ResearchProfile, activate: bool = False) -> ResearchProfile:
        saved = self.store.save_profile(profile)
        if activate or self.store.active_profile() is None:
            self.store.set_active_profile(saved.id)
        return saved

    def sync_profile(self, path: Path) -> ResearchProfile:
        """Load profile/profile.json (written by /setup) into the database and activate it."""
        if not path.is_file():
            raise ServiceError(f"{path} not found. Run /setup in Claude Code to create your research background.")
        text = path.read_text(encoding="utf-8")
        leftovers = sorted(set(PLACEHOLDER.findall(text)))
        if leftovers:
            raise ServiceError(f"{path} still contains template placeholders ({', '.join(leftovers[:5])}). "
                               "Fill them in (or run /setup) before syncing.")
        try:
            profile = ResearchProfile.model_validate_json(text)
        except ValueError as exc:
            raise ServiceError(f"{path} is not a valid profile: {exc}") from None
        saved = self.save_profile(profile, activate=True)
        self.store.set_setting(f"profile_sync:{saved.id}", file_hash(text))
        return saved

    def resolve_profile(self, name: str | None) -> tuple[ResearchProfile | None, bool]:
        """(profile, explicitly requested). Falls back to the active profile."""
        if name:
            try:
                return self.store.get_profile(name), True
            except NotFound as exc:
                raise ServiceError(str(exc)) from None
        return self.store.active_profile(), False

    # ================================================================== folders
    def get_folder(self, name_or_id: str) -> ResearchFolder:
        try:
            return self.store.get_folder(name_or_id)
        except NotFound as exc:
            raise ServiceError(str(exc)) from None

    def create_folder(
        self,
        name: str,
        description: str = "",
        inclusion: Iterable[str] = (),
        exclusion: Iterable[str] = (),
        seeds: Iterable[str] = (),
        profile_name: str | None = None,
        dry_run: bool = False,
    ) -> tuple[ResearchFolder, bool]:
        """Create an application-managed local folder. Returns (folder, created)."""
        profile, _ = self.resolve_profile(profile_name)
        folder = ResearchFolder(
            name=name,
            description=description,
            inclusion_criteria=list(inclusion),
            exclusion_criteria=list(exclusion),
            seed_papers=list(seeds),
            profile_id=profile.id if profile else None,
        )
        destination = f"local paperscout database ({self.settings.db_path})"
        if self.store.find_folder(folder.name) is not None:
            raise ServiceError(f'A local folder named "{folder.name}" already exists; nothing was changed.')
        if dry_run:
            self.store.audit("folder.create", destination, folder.name, True, "previewed", folder.model_dump(mode="json"))
            return folder, False
        try:
            created = self.store.create_folder(folder)
        except Conflict as exc:
            raise ServiceError(str(exc)) from None
        self.store.audit("folder.create", destination, created.id, False, "created", created.model_dump(mode="json"))
        return created, True

    def folder_overview(self) -> list[dict[str, Any]]:
        profiles = {p.id: p.name for p in self.store.list_profiles()}
        rows = []
        for folder in self.store.list_folders():
            members = self.store.memberships(folder.id)
            latest = self.store.latest_feedback([m.paper_id for m in members], folder.profile_id)
            unread = sum(1 for m in members if not (m.paper_id in latest and latest[m.paper_id].label.counts_as_read))
            rows.append(
                {
                    "id": folder.id,
                    "name": folder.name,
                    "description": folder.description,
                    "profile": profiles.get(folder.profile_id) if folder.profile_id else None,
                    "papers": len(members),
                    "unread": unread,
                    "imported": sum(1 for m in members if m.source.startswith("import")),
                    "last_ranked_at": folder.last_ranked_at,
                    "last_discovery_at": folder.last_discovery_at,
                    "inclusion_criteria": folder.inclusion_criteria,
                    "exclusion_criteria": folder.exclusion_criteria,
                    "source_mapping": folder.source_mapping.model_dump() if folder.source_mapping else None,
                }
            )
        return rows

    # ================================================================== paper resolution
    def _local_lookup(self, ref: PaperRef) -> Paper | None:
        if ref.kind == "s2":
            return self.store.get_paper(ref.value)
        if ref.kind == "doi":
            return self.store.find_local_paper(doi=ref.value)
        if ref.kind == "arxiv":
            return self.store.find_local_paper(arxiv_id=ref.value)
        if ref.kind == "corpus":
            return self.store.find_local_paper(corpus_id=int(ref.value))
        return None

    def resolve(self, text: str, *, prefer_local: bool = True) -> Resolution:
        try:
            ref = parse_paper_ref(text)
        except IdentifierError as exc:
            return Resolution("not_found", None, message=str(exc))
        if not ref.is_title:
            if prefer_local:
                local = self._local_lookup(ref)
                if local is not None:
                    return Resolution("resolved", ref, local)
            if ref.kind == "arxiv" and self.uses_arxiv:
                paper = self.arxiv.get_papers([ref.value])[0]
                if paper is None:
                    return Resolution("not_found", ref, message=f"arXiv has no paper {ref.value}.")
                self.store.upsert_paper(paper, "arxiv.id")
                return Resolution("resolved", ref, paper)
            try:
                paper = self.client.get_paper(ref.s2_id)
            except ApiNotFound:
                return Resolution("not_found", ref, message=f"Semantic Scholar has no record for {ref.s2_id}.")
            except ApiError as exc:
                if not self.uses_arxiv:
                    raise
                return Resolution("not_found", ref, message=(
                    f"Looking up {ref.s2_id} needs Semantic Scholar, which is unavailable ({exc}). "
                    "With the arXiv provider, use an arXiv ID or the paper title instead."))
            self.store.upsert_paper(paper, "s2.paper")
            return Resolution("resolved", ref, paper)
        return self._resolve_title(ref)

    def _resolve_title(self, ref: PaperRef) -> Resolution:
        title = ref.value
        options: list[Paper] = []
        source_name = "arXiv" if self.uses_arxiv else "Semantic Scholar"
        if self.uses_arxiv:
            options += self.arxiv.title_search(title)
        else:
            match = self.client.match_title(title)
            if match:
                options.append(Paper.from_s2(match))
            options += [paper for _, paper in self.client.search_papers(title, max_results=5)]
        unique, _ = deduplicate([Candidate(paper=p) for p in options])
        self._canonicalize(unique)
        options = [c.paper for c in unique]
        if not options:
            return Resolution("not_found", ref, message=f"No {source_name} paper matches the title {title!r}.")
        exact = [p for p in options if normalize_title(p.title) == normalize_title(title)]
        if len(exact) == 1:
            self.store.upsert_paper(exact[0], "arxiv.title" if self.uses_arxiv else "s2.match")
            return Resolution("resolved", ref, exact[0])
        close = sorted(options, key=lambda p: _title_similarity(title, p.title), reverse=True)
        close = [p for p in close if _title_similarity(title, p.title) >= 0.5]
        if len(exact) > 1:
            return Resolution("ambiguous", ref, options=exact,
                              message="Several papers share this exact title; choose one by ID.")
        if len(close) == 1 and _title_similarity(title, close[0].title) >= 0.85:
            self.store.upsert_paper(close[0], "arxiv.title" if self.uses_arxiv else "s2.match")
            return Resolution("resolved", ref, close[0])
        if close:
            return Resolution("ambiguous", ref, options=close[:5],
                              message="The title is ambiguous; choose one of these by ID or URL.")
        return Resolution("ambiguous", ref, options=options[:5],
                          message="No close title match; the closest search results are listed.")

    def require_paper(self, text: str) -> Paper:
        res = self.resolve(text)
        if res.status == "resolved" and res.paper:
            return res.paper
        if res.status == "ambiguous":
            listing = "; ".join(f"{p.title} ({p.year or 'year unknown'}) id={p.paper_id}" for p in res.options)
            raise ServiceError(f"{res.message} Options: {listing}")
        raise ServiceError(res.message or f"Could not resolve {text!r}.")

    def _fetch_seed_papers(self, refs: Iterable[str], notices: list[str]) -> list[Paper]:
        papers: list[Paper] = []
        to_fetch: list[tuple[str, str]] = []
        arxiv_ids: list[tuple[str, str]] = []
        for raw in dict.fromkeys(r for r in refs if r and r.strip()):
            try:
                ref = parse_paper_ref(raw)
            except IdentifierError as exc:
                notices.append(f"Ignored seed {raw!r}: {exc}")
                continue
            if ref.is_title:
                notices.append(f"Ignored seed {raw!r}: seeds must be identifiers (DOI, arXiv, S2 id or URL).")
                continue
            local = self._local_lookup(ref)
            if local is not None:
                papers.append(local)
            elif self.uses_arxiv:
                if ref.kind == "arxiv":
                    arxiv_ids.append((raw, ref.value))
                else:
                    notices.append(f"Seed {raw!r} needs Semantic Scholar to look up; with the arXiv provider, "
                                   "give seeds as arXiv IDs.")
            else:
                to_fetch.append((raw, ref.s2_id))
        if arxiv_ids:
            try:
                found = self.arxiv.get_papers([value for _, value in arxiv_ids])
            except ApiError as exc:
                notices.append(f"Could not fetch seed papers from arXiv: {exc}")
                found = [None] * len(arxiv_ids)
            for (raw, _), paper in zip(arxiv_ids, found):
                if paper is None:
                    notices.append(f"Seed {raw!r} was not found on arXiv.")
                    continue
                self.store.upsert_paper(paper, "arxiv.id")
                papers.append(paper)
        if to_fetch:
            try:
                fetched = self.client.get_papers([s2_id for _, s2_id in to_fetch])
            except ApiError as exc:
                notices.append(f"Could not fetch seed papers: {exc}")
                fetched = [None] * len(to_fetch)
            for (raw, _), paper in zip(to_fetch, fetched):
                if paper is None:
                    notices.append(f"Seed {raw!r} was not found on Semantic Scholar.")
                    continue
                self.store.upsert_paper(paper, "s2.batch")
                papers.append(paper)
        return papers

    # ================================================================== ranking context
    def _profile_mode(
        self, criteria_text: str, profile: ResearchProfile | None, explicit: bool, notices: list[str], what: str
    ) -> tuple[bool, str]:
        if profile is None:
            notices.append(
                "No research profile is active, so project usefulness is unknown and ranking is by topic only. "
                "Run /setup in Claude Code to build your research background."
            )
            return False, "topic-only (no profile)"
        overlap = self.scorer.topic_overlap_with_profile(criteria_text, profile)
        threshold = self.config["profile_match"]["min_topic_overlap"]
        if overlap >= threshold:
            return True, f"{what} + profile"
        if explicit:
            notices.append(
                f"The {what} shares little vocabulary with profile '{profile.name}' ({overlap:.0%} of its terms); "
                "project-usefulness judgments may be unreliable."
            )
            return True, f"{what} + profile (low overlap)"
        notices.append(
            f"The {what} differs materially from the active profile '{profile.name}' (only {overlap:.0%} of its "
            f"terms appear in the profile), so it was ranked by {what} only. "
            f"Add --profile \"{profile.name}\" to rank against that profile anyway."
        )
        return False, f"{what}-only (differs from active profile)"

    def _feedback_seeds(self, profile: ResearchProfile | None) -> tuple[list[str], list[str]]:
        pid = profile.id if profile else None
        positive = self.store.feedback_paper_ids(pid, {FeedbackLabel.relevant, FeedbackLabel.deep_study,
                                                        FeedbackLabel.implemented})
        negative = self.store.feedback_paper_ids(pid, {FeedbackLabel.irrelevant, FeedbackLabel.not_useful})
        return positive[-10:], negative[-10:]

    def _build_context(
        self,
        criteria_text: str,
        profile: ResearchProfile | None,
        use_profile: bool,
        folder: ResearchFolder | None,
        extra_seeds: list[str],
        notices: list[str],
    ) -> RankingContext:
        positive_refs = list(extra_seeds)
        negative_refs: list[str] = []
        exclusions: list[str] = []
        if folder:
            positive_refs += folder.seed_papers
            exclusions += folder.exclusion_criteria
        if profile and use_profile:
            positive_refs += profile.positive_seeds
            negative_refs += profile.negative_seeds
            exclusions += profile.exclusion_criteria
            fb_pos, fb_neg = self._feedback_seeds(profile)
            positive_refs += fb_pos
            negative_refs += fb_neg
        return RankingContext(
            topic=criteria_text,
            profile=profile,
            use_profile=use_profile,
            positive_seeds=self._fetch_seed_papers(positive_refs, notices),
            negative_seeds=self._fetch_seed_papers(negative_refs, notices),
            exclusion_criteria=list(dict.fromkeys(exclusions)),
            today=self.today,
            recency_preference=profile.recency_preference if profile and use_profile else RecencyPreference.moderate,
        )

    def _score(
        self, candidates: list[Candidate], ctx: RankingContext, profile_id: str | None
    ) -> list[RankedPaper]:
        ids = [c.paper.paper_id for c in candidates]
        feedback = self.store.latest_feedback(ids, profile_id)
        assessments = self.scorer.assess(candidates, ctx, feedback)
        folders = self.store.folders_for_papers(ids)
        claude = self.store.latest_claude_assessments(ids)
        ranked = [
            RankedPaper(
                paper=c.paper,
                provenance=c.provenance,
                assessment=a,
                folders=folders.get(c.paper.paper_id, []),
                latest_feedback=feedback[c.paper.paper_id].label if c.paper.paper_id in feedback else None,
                claude_assessment=claude.get(c.paper.paper_id),
            )
            for c, a in zip(candidates, assessments)
        ]
        return sorted(ranked, key=self._order_key)

    @staticmethod
    def _order_key(item: RankedPaper) -> tuple:
        a = item.assessment
        search_ranks = [p.rank for p in item.provenance if p.rank]
        return (
            PRIORITY_ORDER[a.priority],
            -(a.heuristic_score if a.heuristic_score is not None else -1),
            min(search_ranks) if search_ranks else 10_000,
        )

    def _persist_run(
        self,
        kind: str,
        query: str | None,
        params: dict[str, Any],
        profile: ResearchProfile | None,
        ranked: list[RankedPaper],
        notices: list[str],
    ) -> str:
        run_id = self.store.create_run(kind, query, params, profile.id if profile else None, self.config.version)
        seen = self.store.previously_surfaced([r.paper.paper_id for r in ranked], exclude_run=run_id)
        for position, item in enumerate(ranked):
            item.seen_before = item.paper.paper_id in seen
            snapshot = self.store.upsert_paper(item.paper, item.provenance[0].source if item.provenance else "local")
            self.store.add_run_candidate(
                run_id,
                item.paper.paper_id,
                position,
                json.dumps([p.model_dump(mode="json") for p in item.provenance]),
                item.assessment.model_dump_json(),
                snapshot,
            )
        self.store.finish_run(run_id, self.client.sources_used + self.arxiv.sources_used, notices)
        return run_id

    # ================================================================== discovery
    def _date_window(self, days: int | None, since: date | None, until: date | None) -> tuple[date, date]:
        if days is not None and since is not None:
            raise ServiceError("Use either --days or --since, not both.")
        end = until or self.today
        start = since or (end - timedelta(days=days if days is not None else self.settings.recent_days))
        if start > end:
            raise ServiceError(f"--since ({start}) is after --until ({end}).")
        return start, end

    def _search(
        self, query: str, ctx: RankingContext, pool: int, window: tuple[date, date] | None, widen: bool = True
    ) -> list[Candidate]:
        """One search with the active provider; provenance records the query and rank."""
        if self.uses_arxiv:
            categories = ctx.profile.arxiv_categories if ctx.profile and ctx.use_profile else []
            try:
                hits = self.arxiv.search_papers(query, max_results=pool, window=window, categories=categories,
                                                widen=widen)
            except ValueError as exc:
                raise ServiceError(f"Cannot build an arXiv query: {exc}.") from None
            except ApiError as exc:
                raise ServiceError(f"arXiv search failed: {exc}") from None
            source = "arxiv.search"
        else:
            date_filter = f"{window[0].isoformat()}:{window[1].isoformat()}" if window else None
            try:
                hits = self.client.search_papers(query, max_results=pool, publication_date_or_year=date_filter)
            except ApiError as exc:
                raise ServiceError(f"Semantic Scholar search failed: {exc}") from None
            source = "s2.search"
        return [Candidate(paper=paper, provenance=[Provenance(source=source, query=query, rank=rank)])
                for rank, paper in hits]

    def _retrieve(
        self,
        queries: list[str],
        ctx: RankingContext,
        pool: int,
        window: tuple[date, date] | None,
        notices: list[str],
    ) -> list[Candidate]:
        """Run one search, or several saved searches, then add recommendations where the provider has them."""
        candidates: list[Candidate] = []
        several = len(queries) > 1
        failures: list[str] = []
        for query in queries:
            try:
                # Widening a sparse search costs an extra request; skip it when several searches share the budget.
                candidates += self._search(query, ctx, pool, window, widen=not several)
            except ServiceError as exc:
                if not several:
                    raise
                failures.append(str(exc))
                notices.append(f"Saved search '{query}' was skipped: {exc}")
        if several and len(failures) == len(queries):
            raise ServiceError(f"All {len(queries)} saved searches failed. First error: {failures[0]}")
        if self.uses_arxiv:
            categories = ctx.profile.arxiv_categories if ctx.profile and ctx.use_profile else []
            if categories:
                notices.append(f"arXiv search limited to your profile's categories: {', '.join(categories)}.")
            if ctx.positive_seeds:
                notices.append("Seed papers were used for text similarity only; seed-based recommendations "
                               "need Semantic Scholar.")
            return candidates
        # Recommendations take Semantic Scholar paper IDs; papers known only from arXiv are skipped.
        positive = [p.paper_id for p in ctx.positive_seeds if S2_PAPER_ID.match(p.paper_id)][:10]
        negative = [p.paper_id for p in ctx.negative_seeds if S2_PAPER_ID.match(p.paper_id)][:10]
        if positive:
            try:
                recs = self.client.recommend(positive, negative, limit=min(pool, 60))
                seed_label = ", ".join(p[:8] for p in positive[:3]) + ("…" if len(positive) > 3 else "")
                for rank, paper in enumerate(recs, 1):
                    candidates.append(Candidate(paper=paper, provenance=[Provenance(
                        source="s2.recommendations", seed=seed_label, rank=rank)]))
            except ApiError as exc:
                notices.append(f"Recommendations unavailable ({exc}); using search results only.")
        return candidates

    def _enrich_missing_abstracts(self, candidates: list[Candidate], notices: list[str]) -> None:
        missing = [c for c in candidates if not c.paper.abstract and S2_PAPER_ID.match(c.paper.paper_id)][:100]
        if not missing or self.uses_arxiv:
            return
        try:
            detailed = self.client.get_papers([c.paper.paper_id for c in missing])
        except ApiError as exc:
            notices.append(f"Could not fetch TLDRs for papers without abstracts: {exc}")
            return
        for cand, paper in zip(missing, detailed):
            if paper is not None and paper.evidence_level.rank > cand.paper.evidence_level.rank:
                cand.paper = paper

    def _filter_reviewed(
        self, candidates: list[Candidate], profile: ResearchProfile | None, include_read: bool
    ) -> tuple[list[Candidate], list[ExcludedPaper]]:
        latest = self.store.latest_feedback([c.paper.paper_id for c in candidates], profile.id if profile else None)
        kept, excluded = [], []
        for cand in candidates:
            event = latest.get(cand.paper.paper_id)
            if event and event.label.counts_as_reviewed and not include_read:
                reason = f"previously reviewed ({event.label.value}"
                reason += f": {event.reason})" if event.reason else ")"
                excluded.append(ExcludedPaper(paper=cand.paper, reason=reason + "; use --include-read to show"))
            else:
                kept.append(cand)
        return kept, excluded

    def _apply_window(
        self, candidates: list[Candidate], window: tuple[date, date]
    ) -> tuple[list[Candidate], list[ExcludedPaper], int]:
        start, end = window
        kept, excluded, year_only = [], [], 0
        for cand in candidates:
            paper = cand.paper
            published = paper.published_on()
            if published is not None:
                if start <= published <= end:
                    kept.append(cand)
                else:
                    excluded.append(ExcludedPaper(paper=paper, reason=f"published {published}, outside the window"))
            elif paper.year is not None and start.year <= paper.year <= end.year:
                year_only += 1
                kept.append(cand)
            elif paper.year is not None:
                excluded.append(ExcludedPaper(paper=paper, reason=f"year {paper.year} is outside the window"))
            else:
                excluded.append(ExcludedPaper(paper=paper, reason="publication date unknown; cannot confirm it is recent"))
        return kept, excluded, year_only

    def _prerequisites(self, ranked: list[RankedPaper], notices: list[str], k: int = 3) -> list[Candidate]:
        if self.uses_arxiv:
            return []
        top = [r for r in ranked if r.assessment.priority in (Priority.p1, Priority.p2)
               and S2_PAPER_ID.match(r.paper.paper_id)][:k]
        counts: dict[str, int] = {}
        papers: dict[str, Paper] = {}
        for item in top:
            try:
                refs = self.client.references(item.paper.paper_id, limit=200)
            except ApiError as exc:
                notices.append(f"Could not fetch references for prerequisite detection: {exc}")
                continue
            for ref in refs:
                counts[ref.paper_id] = counts.get(ref.paper_id, 0) + 1
                papers[ref.paper_id] = ref
        threshold = 2 if len(top) >= 2 else 1
        common = sorted((pid for pid, n in counts.items() if n >= threshold), key=lambda pid: -counts[pid])[:15]
        return [
            Candidate(paper=papers[pid], provenance=[Provenance(
                source="s2.references", detail=f"cited by {counts[pid]} of the top {len(top)} results")])
            for pid in common
        ]

    def _discover_core(
        self,
        kind: str,
        topic: str | None,
        folder_ref: str | None,
        profile_name: str | None,
        limit: int,
        include_read: bool,
        seeds: list[str],
        window: tuple[date, date] | None,
        balanced: bool,
    ) -> RankingReport:
        notices: list[str] = []
        if self.uses_arxiv:
            notices.append(ARXIV_LIMITS)
        elif not self.settings.s2_api_key:
            notices.append("No S2_API_KEY configured: requests used Semantic Scholar's shared unauthenticated pool, "
                           "which is often throttled.")
        folder = self.get_folder(folder_ref) if folder_ref else None
        profile, explicit = self.resolve_profile(profile_name)
        if folder and folder.profile_id and not profile_name:
            try:
                profile, explicit = self.store.get_profile(folder.profile_id), True
            except NotFound:
                pass
        query = topic or (folder and " ".join([folder.name, folder.description]).strip()) or None
        saved = list(profile.search_queries) if (profile and not query) else []
        if saved:
            queries = saved
            query = "; ".join(saved)
            criteria = "\n".join([*saved, *profile.keywords, profile.research_question]).strip()
            notices.append(f"No topic given: ran the {len(saved)} saved searches of profile '{profile.name}' "
                           "and merged the results.")
        else:
            if not query and profile and profile.research_question:
                query = profile.research_question
                notices.append(
                    f"No topic given and profile '{profile.name}' has no saved searches, so its research question "
                    "was used as the search; that tends to give broad results. Add saved searches with "
                    "`/setup --section searches`."
                )
            if not query:
                raise ServiceError("Give a topic (e.g. `recent text-guided 3D scene editing`), --folder, or save "
                                   "searches in your profile with `/setup --section searches`.")
            queries = [query]
            criteria = query if topic else (folder.criteria_text() if folder else query)
        use_profile, mode = self._profile_mode(criteria, profile, explicit, notices, "topic")
        if saved:
            mode = "saved searches + profile" if use_profile else mode
        ctx = self._build_context(criteria, profile, use_profile, folder, seeds, notices)

        pool = min(100, max(limit * 4, 40))
        per_query = pool if len(queries) == 1 else max(15, min(40, pool // len(queries) + 10))
        raw = self._retrieve(queries, ctx, per_query, window, notices)
        candidates, duplicates = deduplicate(raw)
        self._canonicalize(candidates)
        excluded: list[ExcludedPaper] = []
        date_window = None
        if window:
            candidates, out_of_window, year_only = self._apply_window(candidates, window)
            excluded += out_of_window
            date_window = {
                "since": window[0].isoformat(),
                "until": window[1].isoformat(),
                "basis": DATE_BASIS[self.provider],
            }
            if year_only:
                notices.append(f"{year_only} paper(s) have only a publication year; kept but flagged as incomplete dates.")
        candidates, reviewed = self._filter_reviewed(candidates, profile, include_read)
        excluded += reviewed
        self._enrich_missing_abstracts(candidates, notices)
        ranked = self._score(candidates, ctx, profile.id if profile else None)

        if balanced and (profile is None or profile.include_foundational or not use_profile):
            prereq = self._prerequisites(ranked, notices)
            excluded_ids = {e.paper.paper_id for e in excluded}
            prereq = [c for c in prereq if c.paper.paper_id not in excluded_ids]
            if prereq:
                prereq, more_reviewed = self._filter_reviewed(prereq, profile, include_read)
                excluded += more_reviewed
                # Merging keeps both provenances when a prerequisite was also a search hit.
                candidates, extra_dupes = deduplicate(candidates + prereq)
                duplicates += extra_dupes
                ranked = self._score(candidates, ctx, profile.id if profile else None)

        shown = [r for r in ranked if r.assessment.priority is not Priority.skip]
        below = len(ranked) - len(shown)
        selected = shown[:limit]
        if balanced:
            selected = self._balance(shown, selected, limit, notices)
        run_params = {"limit": limit, "include_read": include_read, "folder": folder.name if folder else None,
                      "window": date_window, "seeds": seeds, "pool": pool}
        run_id = self._persist_run(kind, query, run_params, profile, ranked, notices)
        if folder:
            folder.last_discovery_at = utcnow()
            self.store.update_folder(folder)
        if not ranked:
            notices.append("No candidates were found. Try broader terms" + (" or a wider date window." if window else "."))
        subject = f"your saved searches ({profile.name})" if saved else query
        heading = (f"Recent papers: {subject}" if kind == "recent" else f"Discovery: {subject}")
        return RankingReport(
            kind=kind,
            heading=heading,
            run_id=run_id,
            ranking_version=self.config.version,
            profile_name=profile.name if profile else None,
            profile_mode=mode,
            source=SOURCE_LABELS[self.provider],
            query=query,
            date_window=date_window,
            notices=notices,
            items=selected,
            excluded=excluded,
            stats={
                "retrieved": len(raw),
                "duplicates_merged": duplicates,
                "ranked": len(ranked),
                "below_threshold": below,
                "shown": len(selected),
                **self.api_stats(),
            },
        )

    @staticmethod
    def _balance(shown: list[RankedPaper], selected: list[RankedPaper], limit: int, notices: list[str]) -> list[RankedPaper]:
        """Ensure discovery shortlists include context papers (survey / foundational) when available."""
        selected = list(selected)
        wanted = [
            (ReadingRole.survey,),
            (ReadingRole.foundational, ReadingRole.prerequisite),
        ]
        for roles in wanted:
            if any(r.assessment.reading_role in roles for r in selected):
                continue
            extra = next((r for r in shown if r.assessment.reading_role in roles and r not in selected), None)
            if extra is None:
                continue
            if len(selected) >= limit:
                selected.pop()
            selected.append(extra)
            notices.append(f"Added '{extra.paper.title[:60]}' to balance the list with a {extra.assessment.reading_role.value} paper.")
        return selected

    def recent(
        self,
        topic: str | None,
        *,
        days: int | None = None,
        since: date | None = None,
        until: date | None = None,
        folder: str | None = None,
        profile: str | None = None,
        limit: int = 10,
        include_read: bool = False,
        seeds: list[str] | None = None,
    ) -> RankingReport:
        window = self._date_window(days, since, until)
        return self._discover_core("recent", topic, folder, profile, limit, include_read, seeds or [], window, False)

    def discover(
        self,
        topic: str | None,
        *,
        folder: str | None = None,
        profile: str | None = None,
        limit: int = 12,
        include_read: bool = False,
        seeds: list[str] | None = None,
    ) -> RankingReport:
        return self._discover_core("discover", topic, folder, profile, limit, include_read, seeds or [], None, True)

    # ================================================================== folder ranking
    def _folder_candidates(
        self, folder: ResearchFolder, profile_id: str | None, unread: bool, new: bool, imported: bool
    ) -> tuple[list[Candidate], list[ExcludedPaper]]:
        members = self.store.memberships(folder.id)
        papers = self.store.get_papers(m.paper_id for m in members)
        latest = self.store.latest_feedback([m.paper_id for m in members], profile_id)
        kept, excluded = [], []
        for m in members:
            paper = papers.get(m.paper_id)
            if paper is None:
                continue
            event = latest.get(m.paper_id)
            if unread and event and event.label.counts_as_read:
                excluded.append(ExcludedPaper(paper=paper, reason=f"already read ({event.label.value})"))
                continue
            if new and folder.last_ranked_at and m.added_at <= folder.last_ranked_at:
                continue
            if imported and not m.source.startswith("import"):
                continue
            kept.append(Candidate(paper=paper, provenance=[Provenance(
                source="local.folder", retrieved_at=m.added_at,
                detail=f"\"{folder.name}\", added {m.added_at.date()} via {m.source}")]))
        return kept, excluded

    def _filters_label(self, unread: bool, new: bool, imported: bool) -> str:
        parts = [name for flag, name in ((unread, "unread"), (new, "new since last ranking"), (imported, "imported")) if flag]
        return " + ".join(parts) if parts else "all papers"

    def rank_folder(
        self,
        folder_ref: str,
        *,
        profile: str | None = None,
        unread: bool = False,
        new: bool = False,
        imported: bool = False,
        source: str = "local",
        limit: int | None = None,
    ) -> RankingReport:
        check_rank_source(source)
        folder = self.get_folder(folder_ref)
        notices: list[str] = []
        prof, explicit = self.resolve_profile(profile)
        if not profile and folder.profile_id:
            try:
                prof, explicit = self.store.get_profile(folder.profile_id), True
                notices.append(f"Using the folder's associated profile '{prof.name}'.")
            except NotFound:
                pass
        use_profile, mode = self._profile_mode(folder.criteria_text(), prof, explicit, notices, "folder criteria")
        candidates, excluded = self._folder_candidates(folder, prof.id if prof else None, unread, new, imported)
        if not candidates:
            notices.append(
                f'No papers in local folder "{folder.name}" match the filters ({self._filters_label(unread, new, imported)}). '
                "Add papers with `/add <paper> --folder \"...\"` or `paperscout import <file> --folder \"...\"`."
            )
        ctx = self._build_context(folder.criteria_text(), prof, use_profile, folder, [], notices)
        ranked = self._score(candidates, ctx, prof.id if prof else None)
        run_id = self._persist_run("rank-folder", folder.name, {"folder": folder.id, "unread": unread, "new": new,
                                                                  "imported": imported}, prof, ranked, notices)
        folder.last_ranked_at = utcnow()
        self.store.update_folder(folder)
        items = ranked[:limit] if limit else ranked
        return RankingReport(
            kind="rank-folder",
            heading=f'Ranking: local folder "{folder.name}" ({self._filters_label(unread, new, imported)})',
            run_id=run_id,
            ranking_version=self.config.version,
            profile_name=prof.name if prof else None,
            profile_mode=mode,
            source=f'local paperscout folder "{folder.name}" ({folder.id}); not a Semantic Scholar website folder',
            notices=notices,
            items=items,
            excluded=excluded,
            stats={"papers_ranked": len(ranked), "shown": len(items), **self.api_stats()},
        )

    def rank_all(
        self,
        *,
        profile: str | None = None,
        unread: bool = False,
        new: bool = False,
        imported: bool = False,
        source: str = "local",
        limit: int | None = None,
    ) -> RankingReport:
        check_rank_source(source)
        folders = self.store.list_folders()
        notices: list[str] = []
        label = self._filters_label(unread, new, imported)
        if not folders:
            return RankingReport(
                kind="rank-all", heading="Ranking: all local folders", ranking_version=self.config.version,
                profile_mode="n/a", source="local paperscout folders",
                notices=["There are no local research folders yet. Create one with `/folders create \"Name\"` "
                         "or ask for a proposal with `/folders propose <field>`."],
            )
        explicit_profile, explicit = self.resolve_profile(profile)
        folder_profiles = {f.profile_id for f in folders if f.profile_id}
        shared: ResearchProfile | None = None
        if explicit and explicit_profile:
            shared = explicit_profile
        elif len(folder_profiles) == 1:
            try:
                shared = self.store.get_profile(next(iter(folder_profiles)))
            except NotFound:
                shared = None
        elif not folder_profiles:
            shared = explicit_profile  # active profile, if any

        per_folder: dict[str, dict[str, str]] = {}
        sections: list[FolderSection] = []
        all_candidates: list[Candidate] = []
        excluded: list[ExcludedPaper] = []
        for folder in folders:
            fprof = shared
            if fprof is None and folder.profile_id:
                try:
                    fprof = self.store.get_profile(folder.profile_id)
                except NotFound:
                    fprof = None
            cands, excl = self._folder_candidates(folder, fprof.id if fprof else None, unread, new, imported)
            excluded += excl
            local_notes: list[str] = []
            use_profile, _ = self._profile_mode(folder.criteria_text(), fprof, fprof is shared and explicit, local_notes,
                                                "folder criteria")
            ctx = self._build_context(folder.criteria_text(), fprof, use_profile, folder, [], local_notes)
            ranked = self._score(cands, ctx, fprof.id if fprof else None)
            for item in ranked:
                per_folder.setdefault(item.paper.paper_id, {})[folder.name] = item.assessment.priority.value
            sections.append(FolderSection(folder=folder.name, folder_id=folder.id,
                                          profile=fprof.name if fprof else None,
                                          items=ranked[:limit] if limit else ranked,
                                          note=" ".join(local_notes) or None))
            all_candidates += cands
            folder.last_ranked_at = utcnow()
            self.store.update_folder(folder)

        unique, dupes = deduplicate(all_candidates)
        if shared is None:
            notices.append(
                "Folders are associated with different research profiles (or none), so there is no shared goal "
                "to rank against. Showing separate per-folder rankings instead of a single global list. "
                "Use --profile \"<name>\" to rank everything against one profile."
            )
            for section in sections:
                for item in section.items:
                    item.per_folder = per_folder.get(item.paper.paper_id, {})
            run_id = self._persist_run("rank-all", None, {"mode": "per-folder", "filters": label}, None,
                                       [i for s in sections for i in s.items], notices)
            return RankingReport(
                kind="rank-all", heading=f"Ranking: all local folders, per folder ({label})", run_id=run_id,
                ranking_version=self.config.version, profile_mode="per-folder profiles",
                source="local paperscout folders (not Semantic Scholar website folders)",
                notices=notices, sections=sections, excluded=excluded,
                stats={"folders": len(folders), "unique_papers": len(unique), "duplicates_across_folders": dupes},
            )

        notices.append(f"All folders ranked against profile '{shared.name}'. Per-folder priorities are kept for each paper.")
        criteria = "\n".join([shared.topic_text(), shared.research_question]).strip() or shared.name
        ctx = self._build_context(criteria, shared, True, None, [], notices)
        ranked = self._score(unique, ctx, shared.id)
        for item in ranked:
            item.per_folder = per_folder.get(item.paper.paper_id, {})
        run_id = self._persist_run("rank-all", None, {"mode": "global", "filters": label}, shared, ranked, notices)
        return RankingReport(
            kind="rank-all", heading=f"Ranking: all local folders against '{shared.name}' ({label})", run_id=run_id,
            ranking_version=self.config.version, profile_name=shared.name, profile_mode="profile (shared goal)",
            source="local paperscout folders (not Semantic Scholar website folders)",
            notices=notices, items=ranked[:limit] if limit else ranked, excluded=excluded,
            stats={"folders": len(folders), "unique_papers": len(unique), "duplicates_across_folders": dupes},
        )

    # ================================================================== single-paper assessment
    def assess(self, paper_text: str, *, profile: str | None = None, topic: str | None = None) -> RankingReport:
        paper = self.require_paper(paper_text)
        notices: list[str] = []
        prof, explicit = self.resolve_profile(profile)
        criteria = topic or (prof and "\n".join([prof.topic_text(), prof.research_question]).strip()) or None
        if criteria is None:
            notices.append("No profile or --topic given: relevance cannot be judged, only role and evidence.")
            use_profile, mode = False, "none"
        elif topic:
            use_profile, mode = self._profile_mode(topic, prof, explicit, notices, "topic")
        else:
            use_profile, mode = True, "profile"
        cand = Candidate(paper=paper, provenance=[Provenance(source="s2.paper", query=paper_text)])
        if not paper.abstract and not paper.tldr:
            self._enrich_missing_abstracts([cand], notices)
        ctx = self._build_context(criteria or "", prof, use_profile, None, [], notices)
        ranked = self._score([cand], ctx, prof.id if prof else None)
        run_id = self._persist_run("assess", paper_text, {"topic": topic}, prof, ranked, notices)
        return RankingReport(
            kind="assess", heading=f"Assessment: {paper.title}", run_id=run_id, ranking_version=self.config.version,
            profile_name=prof.name if prof else None, profile_mode=mode,
            source="Semantic Scholar paper record (metadata + abstract when available)",
            notices=notices, items=ranked,
        )

    def record_claude_assessment(self, data: dict[str, Any], profile: str | None = None) -> ClaudeAssessment:
        try:
            assessment = ClaudeAssessment.model_validate(data)
        except ValueError as exc:
            raise ServiceError(f"Invalid assessment; nothing stored. {exc}") from None
        paper = self.store.get_paper(assessment.paper_id)
        if paper is None:
            raise ServiceError(f"Unknown paper {assessment.paper_id!r}; resolve it first (e.g. /assess).")
        available = paper.evidence_level
        if assessment.evidence_level.rank > available.rank and assessment.evidence_level.value != "full_text":
            raise ServiceError(
                f"Assessment claims {assessment.evidence_level.value} evidence but only {available.value} is stored."
            )
        prof, _ = self.resolve_profile(profile)
        self.store.add_assessment(
            paper.paper_id, prof.id if prof else None, f"claude:{assessment.model}", assessment.prompt_version,
            assessment.evidence_level.value, assessment.model_dump(mode="json"),
        )
        return assessment

    # ================================================================== folder membership
    def add_to_folder(
        self, paper_text: str, folder_ref: str, *, destination: str = "local", dry_run: bool = False
    ) -> dict[str, Any]:
        if destination == "zotero":
            require("zotero.web_api.write")
        elif destination in ("s2", "s2-library", "semantic-scholar"):
            require("s2.website_library.write")
        elif destination != "local":
            raise ServiceError(f"Unknown destination {destination!r}; supported: local")
        folder = self.store.find_folder(folder_ref)
        if folder is None:
            names = ", ".join(f'"{f.name}"' for f in self.store.list_folders()) or "none yet"
            raise ServiceError(
                f'No local folder named "{folder_ref}". Folders are never created implicitly; '
                f'create it with `/folders create "{folder_ref}"`. Existing local folders: {names}'
            )
        res = self.resolve(paper_text)
        if res.status != "resolved" or res.paper is None:
            return {"status": res.status, "message": res.message,
                    "options": [p.model_dump(mode="json", include={"paper_id", "title", "year", "url"}) for p in res.options]}
        paper = res.paper
        self.store.upsert_paper(paper, "s2.paper")
        target = {"destination": f"local paperscout folder \"{folder.name}\" ({folder.id})",
                  "paper": {"paper_id": paper.paper_id, "title": paper.title, "year": paper.year, "link": paper.link}}
        already = paper.paper_id in {m.paper_id for m in self.store.memberships(folder.id)}
        if dry_run:
            self.store.audit("folder.add", "local", f"{folder.id}:{paper.paper_id}", True, "previewed", target)
            return {"status": "preview", "already_present": already, **target}
        added = self.store.add_to_folder(folder.id, paper.paper_id, "user", "local")
        outcome = "added" if added else "already present (no change)"
        self.store.audit("folder.add", "local", f"{folder.id}:{paper.paper_id}", False, outcome, target)
        return {"status": "added" if added else "unchanged", "message": outcome, **target}

    def remove_from_folder(self, paper_text: str, folder_ref: str, *, dry_run: bool = False) -> dict[str, Any]:
        folder = self.get_folder(folder_ref)
        paper = self.require_paper(paper_text)
        target = {"destination": f"local paperscout folder \"{folder.name}\"", "paper": paper.title}
        if dry_run:
            self.store.audit("folder.remove", "local", f"{folder.id}:{paper.paper_id}", True, "previewed", target)
            return {"status": "preview", **target}
        removed = self.store.remove_from_folder(folder.id, paper.paper_id)
        self.store.audit("folder.remove", "local", f"{folder.id}:{paper.paper_id}", False,
                         "removed" if removed else "not present", target)
        return {"status": "removed" if removed else "unchanged", **target}

    def import_file(
        self,
        path: Path,
        folder_ref: str,
        *,
        dry_run: bool = False,
        mark: str | None = None,
        max_titles: int = 40,
    ) -> dict[str, Any]:
        """Import a BibTeX/RIS/ID-list file into a local folder.

        ``mark`` records a feedback label (e.g. ``already_known`` for a reading-history
        export) on every resolved paper, so discovery stops recommending them.
        """
        folder = self.get_folder(folder_ref)
        if not path.is_file():
            raise ServiceError(f"File not found: {path}")
        try:
            label = parse_feedback_label(mark) if mark else None
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        fmt, entries = read_entries(path)
        id_entries = [e for e in entries if e.ref and not e.ref.is_title]
        title_entries = [e for e in entries if e.ref and e.ref.is_title]
        arxiv_entries: list[ImportEntry] = []
        if self.uses_arxiv:
            # arXiv can look up arXiv IDs; anything else falls back to the entry's title.
            arxiv_entries = [e for e in id_entries if e.ref.kind == "arxiv"]
            for entry in id_entries:
                if entry.ref.kind != "arxiv" and entry.title:
                    title_entries.append(ImportEntry(PaperRef("title", entry.title, entry.raw), entry.title,
                                                     entry.year, entry.raw))
            id_entries = []
        resolved: list[tuple[ImportEntry, Paper]] = []
        problems: list[dict[str, str]] = [{"entry": e.raw, "problem": e.error or "unparseable"} for e in entries if e.ref is None]
        if arxiv_entries:
            try:
                found = self.arxiv.get_papers([e.ref.value for e in arxiv_entries])
            except ApiError as exc:
                raise ServiceError(f"arXiv lookup failed: {exc}") from None
            for entry, paper in zip(arxiv_entries, found):
                if paper is None:
                    problems.append({"entry": entry.raw, "problem": f"arXiv:{entry.ref.value} not found on arXiv"})
                else:
                    resolved.append((entry, paper))
        if self.uses_arxiv:
            skipped = [e for e in entries if e.ref and not e.ref.is_title and e.ref.kind != "arxiv" and not e.title]
            problems += [{"entry": e.raw, "problem": "needs Semantic Scholar (no arXiv ID or title to search)"}
                         for e in skipped]
        if id_entries:
            try:
                papers = self.client.get_papers([e.ref.s2_id for e in id_entries])
            except ApiError as exc:
                raise ServiceError(f"Semantic Scholar lookup failed: {exc}") from None
            for entry, paper in zip(id_entries, papers):
                if paper is None:
                    problems.append({"entry": entry.raw, "problem": f"{entry.ref.s2_id} not found on Semantic Scholar"})
                else:
                    resolved.append((entry, paper))
        for entry in title_entries[:max_titles]:
            res = self._resolve_title(entry.ref)
            if res.status == "resolved" and res.paper:
                resolved.append((entry, res.paper))
            else:
                problems.append({"entry": entry.raw, "problem": f"title {res.status}: {res.message}"})
        if len(title_entries) > max_titles:
            problems.append({"entry": f"{len(title_entries) - max_titles} more title-only entries",
                             "problem": f"skipped (max {max_titles} title lookups per import to respect rate limits)"})
        candidates = [Candidate(paper=paper) for _, paper in resolved]
        self._canonicalize(candidates)
        resolved = [(entry, cand.paper) for (entry, _), cand in zip(resolved, candidates)]
        added, unchanged, marked = [], [], 0
        profile = self.store.active_profile()
        latest = self.store.latest_feedback([p.paper_id for _, p in resolved], profile.id if profile else None)
        for _entry, paper in resolved:
            self.store.upsert_paper(paper, f"import:{fmt}")
            if dry_run:
                continue
            if self.store.add_to_folder(folder.id, paper.paper_id, "import", f"import:{fmt}", note=path.name):
                added.append(paper.title)
            else:
                unchanged.append(paper.title)
            previous = latest.get(paper.paper_id)
            if label and (previous is None or previous.label is not label):  # idempotent re-imports
                self.store.add_feedback(FeedbackEvent(
                    paper_id=paper.paper_id, label=label, reason=f"imported from {path.name}",
                    profile_id=profile.id if profile else None, folder_id=folder.id,
                ))
                marked += 1
        self.store.audit("folder.import", "local", folder.id, dry_run, "previewed" if dry_run else "imported",
                         {"file": str(path), "format": fmt, "resolved": len(resolved), "problems": len(problems),
                          "mark": label.value if label else None})
        return {
            "destination": f'local paperscout folder "{folder.name}" ({folder.id})',
            "format": fmt,
            "entries": len(entries),
            "resolved": [p.title for _, p in resolved],
            "added": added,
            "already_present": unchanged,
            "marked": {"label": label.value, "papers": marked} if label else None,
            "problems": problems,
            "dry_run": dry_run,
        }

    # ================================================================== feedback
    def record_feedback(
        self,
        paper_text: str,
        label_text: str,
        reason: str | None = None,
        *,
        profile: str | None = None,
        folder: str | None = None,
    ) -> tuple[FeedbackEvent, Paper, list[FeedbackEvent]]:
        try:
            label = parse_feedback_label(label_text)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        paper = self.require_paper(paper_text)
        prof, _ = self.resolve_profile(profile)
        fld = self.get_folder(folder) if folder else None
        event = self.store.add_feedback(FeedbackEvent(
            paper_id=paper.paper_id, label=label, reason=(reason or None),
            profile_id=prof.id if prof else None, folder_id=fld.id if fld else None,
        ))
        return event, paper, self.store.feedback_history(paper.paper_id)


def band_at_least(band: Band, minimum: Band) -> bool:
    order = [Band.unknown, Band.low, Band.medium, Band.high]
    return order.index(band) >= order.index(minimum)
