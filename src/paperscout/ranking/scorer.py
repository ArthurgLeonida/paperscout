"""Transparent heuristic scoring.

Each dimension (topic relevance, project usefulness, seed affinity, recency)
is computed and reported separately with its basis. Unknown dimensions are
reported as unknown and excluded from the combined ordering score (weights are
renormalised) rather than treated as zero. Citation counts are used only to
label a paper's likely *reading role*, never to rank it.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from paperscout.models import (
    Band,
    Candidate,
    Component,
    EvidenceLevel,
    FeedbackEvent,
    HeuristicAssessment,
    KnowledgeLevel,
    Paper,
    Priority,
    ReadingDepth,
    ReadingRole,
    RecencyPreference,
    ResearchProfile,
    TopicKnowledge,
)
from paperscout.ranking.text import (
    TfidfSpace,
    bigrams,
    coverage,
    expand_synonyms,
    phrase_matches,
    phrase_terms,
    tokenize,
)

DEFAULT_CONFIG = "heuristic-v2"

_SURVEY = re.compile(r"\b(survey|review|overview|tutorial|primer|state of the art)\b", re.IGNORECASE)
_BENCH = re.compile(r"\b(benchmark|dataset|challenge|leaderboard)\b", re.IGNORECASE)
BACKGROUND_ROLES = (ReadingRole.foundational, ReadingRole.prerequisite, ReadingRole.survey)


@dataclass(frozen=True)
class RankingConfig:
    raw: dict[str, Any]

    @property
    def version(self) -> str:
        return self.raw["version"]

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]


def load_ranking_config(path: Path | None = None, name: str = DEFAULT_CONFIG) -> RankingConfig:
    if path is not None:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    else:
        data = tomllib.loads((resources.files("paperscout.ranking") / f"{name}.toml").read_text(encoding="utf-8"))
    for section in ("weights", "priority_thresholds", "band_thresholds", "effort_minutes"):
        if section not in data:
            raise ValueError(f"ranking config missing [{section}]")
    return RankingConfig(data)


@dataclass
class RankingContext:
    """What the candidates are judged against. Built by the service layer."""

    topic: str | None = None
    profile: ResearchProfile | None = None
    use_profile: bool = False
    positive_seeds: list[Paper] = field(default_factory=list)
    negative_seeds: list[Paper] = field(default_factory=list)
    exclusion_criteria: list[str] = field(default_factory=list)
    today: date = field(default_factory=date.today)
    recency_preference: RecencyPreference = RecencyPreference.moderate


def doc_text(paper: Paper) -> str:
    body = paper.abstract or paper.tldr or ""
    return f"{paper.title}\n{body}"


class HeuristicScorer:
    def __init__(self, config: RankingConfig):
        self.config = config
        self.synonyms: dict[str, list[str]] = dict(config.raw.get("synonyms", {}))

    # ------------------------------------------------------------------ helpers
    def band(self, value: float | None) -> Band:
        if value is None:
            return Band.unknown
        thresholds = self.config["band_thresholds"]
        if value >= thresholds["high"]:
            return Band.high
        if value >= thresholds["medium"]:
            return Band.medium
        return Band.low

    def topic_overlap_with_profile(self, topic: str, profile: ResearchProfile) -> float:
        """Share of topic terms present anywhere in the profile text (0..1)."""
        topic_tokens = tokenize(expand_synonyms(topic, self.synonyms))
        profile_text = expand_synonyms(
            "\n".join([profile.topic_text(), profile.goal_text(), profile.name]), self.synonyms
        )
        value, _ = coverage(topic_tokens, set(tokenize(profile_text)))
        return value

    # ------------------------------------------------------------------ scoring
    def assess(
        self,
        candidates: list[Candidate],
        ctx: RankingContext,
        feedback: dict[str, FeedbackEvent] | None = None,
    ) -> list[HeuristicAssessment]:
        feedback = feedback or {}
        doc_tokens = [tokenize(expand_synonyms(doc_text(c.paper), self.synonyms)) for c in candidates]
        seed_tokens = [
            tokenize(expand_synonyms(doc_text(p), self.synonyms)) for p in ctx.positive_seeds + ctx.negative_seeds
        ]
        space = TfidfSpace(doc_tokens + seed_tokens)
        pos_vecs = [space.vector(t) for t in seed_tokens[: len(ctx.positive_seeds)]]
        neg_vecs = [space.vector(t) for t in seed_tokens[len(ctx.positive_seeds):]]

        topic_tokens = tokenize(expand_synonyms(ctx.topic, self.synonyms)) if ctx.topic else []
        topic_vec = space.vector(topic_tokens) if topic_tokens else {}
        profile = ctx.profile if ctx.use_profile else None
        goal_vec: dict[str, float] = {}
        keyword_phrases: list[list[str]] = []
        if profile:
            goal_vec = space.vector(tokenize(expand_synonyms(profile.goal_text(), self.synonyms)))
            keyword_phrases = phrase_terms(profile.keywords + profile.inclusion_criteria)
        exclusion_phrases = [(c, toks) for c in ctx.exclusion_criteria for toks in [tokenize(c)] if toks]
        knowledge = self._knowledge_variants(profile) if profile else []

        results = []
        for cand, tokens in zip(candidates, doc_tokens):
            paper = cand.paper
            token_set = set(tokens)
            title_tokens = set(tokenize(expand_synonyms(paper.title, self.synonyms)))
            vec = space.vector(tokens)
            uncertainties: list[str] = []

            topic = self._topic_component(topic_tokens, topic_vec, token_set, title_tokens, vec, tokens)
            useful = self._usefulness_component(profile, goal_vec, keyword_phrases, token_set, vec, ctx)
            seed = self._seed_component(cand, pos_vecs, neg_vecs, vec)
            recency = self._recency_component(paper, ctx)

            exclusion_hits = [c for c, toks in exclusion_phrases if all(t in token_set for t in toks)]
            score = self._combine(
                {"topic_relevance": topic, "project_usefulness": useful, "seed_affinity": seed, "recency": recency},
                ctx.recency_preference,
            )
            neg_closer = seed.basis.startswith("closer to a negative seed")
            if score is not None:
                if exclusion_hits:
                    score *= self.config["penalties"]["exclusion_hit"]
                if neg_closer:
                    score *= self.config["penalties"]["negative_seed"]

            role, role_basis = self._role(paper, cand, ctx.today)
            priority = self._priority(score)
            fb = feedback.get(paper.paper_id)
            feedback_note = None
            if fb is not None:
                feedback_note = f"Your latest feedback: {fb.label.value}" + (f" — {fb.reason}" if fb.reason else "")
                if fb.label.negative:
                    priority = Priority.skip
            depth = self._depth(priority, role, topic, useful, seed)
            depth, background_note = self._knowledge_adjustment(depth, role, priority, token_set, knowledge)
            effort = self._effort(depth, role)

            evidence = paper.evidence_level
            uncertainties.extend(self._uncertainties(paper, evidence, role, topic, useful, seed, recency, ctx))
            if exclusion_hits:
                uncertainties.append(
                    "Matches exclusion criteria (" + "; ".join(exclusion_hits) + ") — check whether that applies."
                )
            confidence = self._confidence(evidence, [topic, useful, seed, recency])

            matched = sorted(t for t in set(topic_tokens) if t in token_set)
            results.append(
                HeuristicAssessment(
                    method=self.config.version,
                    topic_relevance=topic,
                    project_usefulness=useful,
                    seed_affinity=seed,
                    recency=recency,
                    evidence_level=evidence,
                    confidence=confidence,
                    reading_role=role,
                    role_basis=role_basis,
                    priority=priority,
                    heuristic_score=round(score, 2) if score is not None else None,
                    reading_depth=depth,
                    effort_minutes=effort,
                    matched_terms=matched,
                    exclusion_hits=exclusion_hits,
                    feedback_note=feedback_note,
                    background_note=background_note,
                    uncertainties=uncertainties,
                    next_action=self._next_action(paper, priority, depth, role, fb),
                )
            )
        return results

    def _topic_component(self, topic_tokens, topic_vec, token_set, title_tokens, vec, tokens) -> Component:
        if not topic_tokens:
            return Component(basis="no topic or folder criteria given")
        distinct = list(dict.fromkeys(topic_tokens))
        if len(distinct) <= 12:
            cov_all, matched = coverage(topic_tokens, token_set)
            cov_title, _ = coverage(topic_tokens, title_tokens)
            topic_bigrams = set(bigrams(topic_tokens))
            doc_bigrams = set(bigrams(tokens))
            phrase = len(topic_bigrams & doc_bigrams) / len(topic_bigrams) if topic_bigrams else cov_all
            value = 0.6 * cov_all + 0.25 * cov_title + 0.15 * phrase
            basis = f"{len(matched)}/{len(distinct)} topic terms matched (title {round(cov_title * len(distinct))})"
        else:
            cos = TfidfSpace.cosine(topic_vec, vec)
            value = min(1.0, cos / 0.35)
            _, matched = coverage(topic_tokens, token_set)
            basis = f"TF-IDF similarity to criteria text; shared terms: {', '.join(matched[:8]) or 'none'}"
        return Component(value=round(value, 2), band=self.band(value), basis=basis)

    def _usefulness_component(self, profile, goal_vec, keyword_phrases, token_set, vec, ctx) -> Component:
        if profile is None:
            reason = "no research profile applied (topic-only ranking)"
            return Component(basis=reason)
        if not goal_vec and not keyword_phrases:
            return Component(basis=f"profile '{profile.name}' has no goal text or keywords")
        cos_score = min(1.0, TfidfSpace.cosine(goal_vec, vec) / 0.25) if goal_vec else None
        hits = [" ".join(p) for p in keyword_phrases if phrase_matches(p, token_set)]
        kw_score = min(1.0, len(hits) / 2) if keyword_phrases else None
        parts = [v for v in (cos_score, kw_score) if v is not None]
        value = sum(parts) / len(parts)
        basis = f"profile '{profile.name}': keyword/criteria hits [{', '.join(hits[:5]) or 'none'}]"
        if cos_score is not None:
            basis += f"; goal-text similarity {self.band(cos_score).value}"
        return Component(value=round(value, 2), band=self.band(value), basis=basis)

    def _seed_component(self, cand: Candidate, pos_vecs, neg_vecs, vec) -> Component:
        rec_ranks = [p.rank for p in cand.provenance if p.source.startswith("s2.recommendations") and p.rank]
        if not pos_vecs and not rec_ranks:
            return Component(basis="no seed papers available")
        pos = max((TfidfSpace.cosine(v, vec) for v in pos_vecs), default=0.0)
        neg = max((TfidfSpace.cosine(v, vec) for v in neg_vecs), default=0.0)
        value = min(1.0, pos / 0.4)
        basis = f"text similarity to positive seeds {self.band(value).value}"
        if rec_ranks:
            rec_value = max(0.5, 0.9 - 0.02 * (min(rec_ranks) - 1))
            if rec_value > value:
                value = rec_value
                basis = f"recommended from your seed papers (rank {min(rec_ranks)})"
        if neg_vecs and neg > pos and neg > 0.2:
            basis = "closer to a negative seed than to positive seeds; " + basis
        return Component(value=round(value, 2), band=self.band(value), basis=basis)

    def _recency_component(self, paper: Paper, ctx: RankingContext) -> Component:
        half_life = self.config["recency_half_life_days"][ctx.recency_preference.value]
        published = paper.published_on()
        if published is not None:
            age = max(0, (ctx.today - published).days)
            basis = f"published {published.isoformat()} ({age} days ago)"
        elif paper.year is not None:
            age = max(0, (ctx.today - date(paper.year, 7, 1)).days)
            basis = f"only the year is known ({paper.year}); age approximated from mid-year"
        else:
            return Component(basis="publication date unknown")
        value = 0.5 ** (age / half_life)
        return Component(value=round(value, 2), band=self.band(value), basis=basis)

    def _combine(self, components: dict[str, Component], preference: RecencyPreference) -> float | None:
        weights = dict(self.config["weights"])
        weights["recency"] *= self.config["recency_preference_factor"][preference.value]
        known = {k: c.value for k, c in components.items() if c.value is not None and weights.get(k, 0) > 0}
        if "topic_relevance" not in known and "project_usefulness" not in known:
            return None
        total = sum(weights[k] for k in known)
        return sum(weights[k] * v for k, v in known.items()) / total if total else None

    def _priority(self, score: float | None) -> Priority:
        if score is None:
            return Priority.unknown
        t = self.config["priority_thresholds"]
        if score >= t["P1"]:
            return Priority.p1
        if score >= t["P2"]:
            return Priority.p2
        if score >= t["P3"]:
            return Priority.p3
        return Priority.skip

    def _role(self, paper: Paper, cand: Candidate, today: date) -> tuple[ReadingRole, str]:
        cfg = self.config["roles"]
        if any(p.source == "s2.references" for p in cand.provenance):
            return ReadingRole.prerequisite, "cited by a seed/target paper (retrieved from its reference list)"
        if paper.publication_types and "Review" in paper.publication_types:
            return ReadingRole.survey, "publication type 'Review' in metadata"
        if _SURVEY.search(paper.title):
            return ReadingRole.survey, "title suggests a survey/overview"
        if _BENCH.search(paper.title):
            return ReadingRole.benchmark_dataset, "title mentions a benchmark/dataset"
        age_days = None
        if paper.published_on():
            age_days = (today - paper.published_on()).days
        elif paper.year:
            age_days = (today - date(paper.year, 7, 1)).days
        if (
            age_days is not None
            and age_days >= 365 * cfg["foundational_min_age_years"]
            and (paper.citation_count or 0) >= cfg["foundational_min_citations"]
        ):
            return ReadingRole.foundational, (
                f"older ({paper.year}) and widely cited — role label only; citations do not raise priority"
            )
        if age_days is not None and age_days <= cfg["recent_method_max_age_days"]:
            return ReadingRole.recent_method, "recent paper; role assumed to be a method contribution"
        if paper.abstract:
            return ReadingRole.method, "default role for a non-survey research paper"
        return ReadingRole.unknown, "insufficient metadata to infer a role"

    def _knowledge_variants(self, profile: ResearchProfile) -> list[tuple[TopicKnowledge, list[list[str]]]]:
        """Each knowledge-map topic with the token lists that count as a mention (topic or its synonyms)."""
        out = []
        for item in profile.knowledge:
            phrases = [item.topic, *self.synonyms.get(item.topic.lower(), [])]
            variants = [toks for toks in (tokenize(p) for p in phrases) if toks]
            if variants:
                out.append((item, variants))
        return out

    def _knowledge_adjustment(self, depth, role, priority, token_set, knowledge) -> tuple[ReadingDepth, str | None]:
        """Adjust reading depth (never priority) for what the researcher already knows."""
        if depth is ReadingDepth.skip or not knowledge:
            return depth, None
        matched = [item for item, variants in knowledge if any(all(t in token_set for t in v) for v in variants)]
        if not matched:
            return depth, None
        expert = [f"'{k.topic}'" for k in matched if k.level is KnowledgeLevel.expert]
        new = [f"'{k.topic}'" for k in matched if k.level is KnowledgeLevel.new]
        if role in BACKGROUND_ROLES:
            if expert and depth in (ReadingDepth.substantive, ReadingDepth.deep):
                return ReadingDepth.overview, (
                    f"Depth reduced to overview: your knowledge map marks you as expert in {', '.join(expert)}, "
                    f"so this {role.value} paper is likely review material."
                )
            if new and depth is ReadingDepth.overview and priority in (Priority.p1, Priority.p2):
                return ReadingDepth.substantive, (
                    f"Depth raised to substantive: your knowledge map marks {', '.join(new)} as new to you, "
                    f"so this {role.value} paper is worth a proper read."
                )
            return depth, None
        if new:
            return depth, (
                f"Builds on {', '.join(new)}, which your knowledge map marks as new: "
                "consider reading its prerequisites first (/discover can surface them)."
            )
        return depth, None

    def _depth(self, priority, role, topic, useful, seed) -> ReadingDepth:
        if priority is Priority.skip:
            return ReadingDepth.skip
        if role is ReadingRole.survey:
            return ReadingDepth.overview
        if priority is Priority.p1:
            strong = [c for c in (topic, useful, seed) if c.band is Band.high]
            return ReadingDepth.deep if len(strong) >= 2 and role is not ReadingRole.benchmark_dataset else ReadingDepth.substantive
        if priority is Priority.p2 and role in (ReadingRole.foundational, ReadingRole.prerequisite):
            return ReadingDepth.substantive
        return ReadingDepth.overview

    def _effort(self, depth: ReadingDepth, role: ReadingRole) -> tuple[int, int]:
        eff = self.config["effort_minutes"]
        if depth is ReadingDepth.skip:
            return (0, 0)
        if depth is ReadingDepth.overview and role is ReadingRole.survey:
            return tuple(eff["survey_overview"])
        return tuple(eff[depth.value])

    def _confidence(self, evidence: EvidenceLevel, components: list[Component]) -> Band:
        unknown = sum(1 for c in components if c.value is None)
        if evidence is EvidenceLevel.metadata:
            return Band.low
        if evidence is EvidenceLevel.tldr or unknown >= 3:
            return Band.low
        # Lexical heuristics never claim high confidence; a Claude review can add context.
        return Band.medium

    def _uncertainties(self, paper, evidence, role, topic, useful, seed, recency, ctx) -> list[str]:
        notes = []
        if evidence is EvidenceLevel.metadata:
            notes.append("No abstract or TLDR available: judged from title/metadata only.")
        elif evidence is EvidenceLevel.tldr:
            notes.append("No abstract available; used Semantic Scholar's machine-generated TLDR.")
        if paper.date_precision == "year":
            notes.append("Publication date incomplete (year only).")
        elif paper.date_precision == "unknown":
            notes.append("Publication date unknown.")
        if role is not ReadingRole.prerequisite:
            notes.append(f"Reading role '{role.value}' is inferred from metadata (heuristic).")
        if useful.value is None and ctx.profile is not None and not ctx.use_profile:
            notes.append("Project usefulness not judged: the topic differs from the active profile.")
        recent = paper.published_on() and (ctx.today - paper.published_on()).days < 365
        if paper.citation_count is None:
            if not recent and role is not ReadingRole.survey:
                notes.append("Citation count unknown (the source provides none), so foundational status was not inferred.")
        elif recent and paper.citation_count < 10:
            notes.append("Few citations is expected for a recent paper and was not penalised.")
        return notes

    def _next_action(self, paper: Paper, priority, depth, role, fb) -> str:
        if fb is not None and fb.label.negative:
            return "Skip (you marked it as not relevant/useful)."
        if fb is not None and fb.label.counts_as_read:
            return f"Already read ({fb.label.value}); revisit only if needed."
        if depth is ReadingDepth.skip:
            return "Skip unless the topic scope changes."
        access = "open-access PDF available" if paper.open_access_pdf_url else "check access via the source link"
        if depth is ReadingDepth.overview:
            return f"Overview pass: abstract, intro, figures, conclusion ({access})."
        if depth is ReadingDepth.substantive:
            return f"Substantive read: method + main experiments; note open questions ({access})."
        return f"Deep study: full method, ablations, and limitations; consider reproducing ({access})."
