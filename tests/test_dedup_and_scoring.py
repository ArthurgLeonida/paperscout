
from datetime import datetime, timezone

from paperscout.dedup import deduplicate
from paperscout.models import (
    CRITERIA,
    Band,
    Candidate,
    EvidenceLevel,
    FeedbackEvent,
    FeedbackLabel,
    Paper,
    Priority,
    Provenance,
    ReadingRole,
    RecencyPreference,
    ResearchProfile,
    StoredJudgment,
)
from paperscout.ranking.scorer import HeuristicScorer, RankingContext, load_ranking_config

from .conftest import PAPERS, TODAY, public


def paper(n: int, **changes) -> Paper:
    return Paper.from_s2(public(PAPERS[n])).model_copy(update=changes)


def cand(p: Paper, source="s2.search", rank=1) -> Candidate:
    return Candidate(paper=p, provenance=[Provenance(source=source, rank=rank)])


# ----------------------------------------------------------------------------- dedup


def test_dedup_merges_provenance_by_paper_id():
    merged, dupes = deduplicate([cand(paper(1)), cand(paper(1), "s2.recommendations", 4)])
    assert dupes == 1 and len(merged) == 1
    assert [p.source for p in merged[0].provenance] == ["s2.search", "s2.recommendations"]


def test_dedup_by_doi_and_arxiv_and_title_year():
    a = paper(3)
    b = paper(3, paper_id="f" * 40, corpus_id=None)  # same DOI, different S2 id
    c = paper(1, paper_id="e" * 40, corpus_id=None, external_ids={"ArXiv": "2609.00001"})
    d = paper(2, paper_id="d" * 40, corpus_id=None, external_ids={})  # same title + year as paper 2
    merged, dupes = deduplicate([cand(a), cand(b), cand(paper(1)), cand(c), cand(paper(2)), cand(d)])
    assert dupes == 3 and len(merged) == 3


def test_same_title_different_year_is_not_merged():
    merged, dupes = deduplicate([cand(paper(1)), cand(paper(10))])
    assert dupes == 0 and len(merged) == 2


def test_dedup_keeps_richest_metadata():
    bare = paper(1, abstract=None)
    merged, _ = deduplicate([cand(bare), cand(paper(1))])
    assert merged[0].paper.abstract


# ----------------------------------------------------------------------------- scoring

CONFIG = load_ranking_config()
SCORER = HeuristicScorer(CONFIG)
PROFILE = ResearchProfile(
    name="3D Editing",
    field="3D scene editing",
    research_question="text-guided editing of Gaussian splatting scenes with background preservation",
    keywords=["gaussian splatting", "scene editing"],
    recency_preference=RecencyPreference.strong,
)


def assess(cands, topic="text guided gaussian splatting scene editing", **ctx_kwargs):
    ctx = RankingContext(topic=topic, profile=PROFILE, use_profile=True, today=TODAY, **ctx_kwargs)
    return SCORER.assess(cands, ctx)


def test_config_is_versioned():
    assert CONFIG.version == "score-v3"


def test_missing_abstract_is_metadata_evidence_with_low_confidence():
    a = assess([cand(paper(1, abstract=None, tldr=None))])[0]
    assert a.evidence_level is EvidenceLevel.metadata
    assert a.confidence is Band.low
    assert any("title/metadata only" in u for u in a.uncertainties)


def test_unknown_date_is_unknown_not_zero():
    a = assess([cand(paper(11))])[0]
    assert a.recency.value is None and a.recency.band is Band.unknown
    assert a.score is not None  # renormalised over known criteria
    assert any("date unknown" in u for u in a.uncertainties)


def test_year_only_date_is_flagged():
    a = assess([cand(paper(7))])[0]
    assert "year is known" in a.recency.basis
    assert any("year only" in u for u in a.uncertainties)


def test_recent_low_citation_paper_not_penalised_vs_cited_older():
    recent, old = assess([cand(paper(1)), cand(paper(3))])
    assert recent.score > old.score
    assert any("not penalised" in u for u in recent.uncertainties)


def test_citations_only_label_roles():
    old = assess([cand(paper(9))], topic="neural radiance fields view synthesis")[0]
    assert old.reading_role is ReadingRole.foundational
    assert "widely cited" in old.role_basis and old.criteria["foundation"].value == 75


def test_survey_role_and_overview_depth():
    a = assess([cand(paper(4))])[0]
    assert a.reading_role is ReadingRole.survey
    assert a.reading_depth.value in ("overview", "skip")


def test_exclusion_criteria_penalise_and_flag():
    plain = assess([cand(paper(5))])[0]
    excluded = assess([cand(paper(5))], exclusion_criteria=["character animation"])[0]
    assert excluded.exclusion_hits == ["character animation"]
    assert excluded.score < plain.score


def test_negative_feedback_forces_skip():
    ctx = RankingContext(topic="gaussian splatting scene editing", profile=PROFILE, use_profile=True, today=TODAY)
    fb = {PAPERS[1]["paperId"]: FeedbackEvent(paper_id=PAPERS[1]["paperId"], label=FeedbackLabel.irrelevant,
                                              reason="not my scope")}
    a = SCORER.assess([cand(paper(1))], ctx, fb)[0]
    assert a.priority is Priority.skip and "not my scope" in a.feedback_note


def test_topic_only_mode_leaves_usefulness_unknown():
    ctx = RankingContext(topic="medical image segmentation", profile=PROFILE, use_profile=False, today=TODAY)
    a = SCORER.assess([cand(paper(8))], ctx)[0]
    assert a.project_usefulness.value is None
    assert any("differs from the active profile" in u for u in a.uncertainties)


def test_profile_overlap_detection():
    assert SCORER.topic_overlap_with_profile("gaussian splatting editing", PROFILE) >= 0.5
    assert SCORER.topic_overlap_with_profile("medical image segmentation", PROFILE) < 0.25


def test_recommendation_provenance_counts_as_seed_affinity():
    a = assess([cand(paper(2), "s2.recommendations", 1)])[0]
    assert a.seed_affinity.value >= 0.8 and "recommended" in a.seed_affinity.basis


def test_heuristic_confidence_never_high():
    for a in assess([cand(paper(n)) for n in (1, 2, 3, 4)]):
        assert a.confidence is not Band.high


# ----------------------------------------------------------------------------- 1-100 score (score-v3)


def judged(paper_id: str, criterion: str, score, confidence="high") -> dict:
    return {paper_id: {criterion: StoredJudgment(
        criterion=criterion, score=score, confidence=confidence, basis="abstract", evidence="Synthetic rating.",
        judge="claude-test", prompt_version=f"{criterion}-judge-v1", run_id="run_test",
        created_at=datetime(2026, 11, 1, tzinfo=timezone.utc))}}


def test_score_is_1_to_100_with_every_criterion_and_weights_summing_to_one():
    for a in assess([cand(paper(n)) for n in (1, 3, 4, 9, 11)]):
        assert 1 <= a.score <= 100 and isinstance(a.score, int)
        assert list(a.criteria) == list(CRITERIA)
        assert abs(sum(c.weight for c in a.criteria.values()) - 1) < 0.01
        assert all(c.weight == 0 for c in a.criteria.values() if c.value is None)


def test_popularity_is_unknown_for_new_papers_and_log_scaled_for_older_ones():
    new, mid, top = assess([cand(paper(1)), cand(paper(4)), cand(paper(9))])  # 3, ~82/yr, ~1200/yr citations
    assert new.criteria["popularity"].value is None and "too new to judge" in new.criteria["popularity"].basis
    assert 60 <= mid.criteria["popularity"].value < top.criteria["popularity"].value == 100
    assert "Semantic Scholar" in mid.criteria["popularity"].basis


def test_missing_citation_counts_leave_popularity_and_foundation_unknown():
    a = assess([cand(paper(9, citation_count=None, citation_source=None))])[0]
    assert a.criteria["popularity"].value is None and "no citation count" in a.criteria["popularity"].basis
    assert a.criteria["foundation"].value is None


def test_foundation_comes_from_reference_lists_or_wide_citation():
    shared = cand(paper(3), "s2.references")
    shared.provenance[0].detail = "cited by 2 of the top 3 results"
    single = cand(paper(4), "s2.references")
    single.provenance[0].detail = "cited by 1 of the top 1 results"
    a_shared, a_single, a_cited = assess([shared, single, cand(paper(9))])
    assert a_shared.criteria["foundation"].value == CONFIG["foundation"]["prerequisite_shared"]
    assert a_single.criteria["foundation"].value == CONFIG["foundation"]["prerequisite_single"]
    assert a_cited.criteria["foundation"].value == CONFIG["foundation"]["widely_cited"]


def test_judge_rating_replaces_the_computed_criterion():
    plain = assess([cand(paper(1))])[0]
    ctx = RankingContext(topic="text guided gaussian splatting scene editing", profile=PROFILE, use_profile=True,
                         today=TODAY)
    rated = SCORER.assess([cand(paper(1))], ctx, judgments=judged(PAPERS[1]["paperId"], "relevance", 12))[0]
    assert rated.criteria["relevance"].value == 12 and rated.criteria["relevance"].judged
    assert rated.criteria["usefulness"] == plain.criteria["usefulness"]
    assert rated.score < plain.score


def test_null_judge_rating_keeps_the_computed_value():
    plain = assess([cand(paper(1))])[0]
    ctx = RankingContext(topic="text guided gaussian splatting scene editing", profile=PROFILE, use_profile=True,
                         today=TODAY)
    rated = SCORER.assess([cand(paper(1))], ctx, judgments=judged(PAPERS[1]["paperId"], "relevance", None, "low"))[0]
    assert rated.criteria["relevance"].value == plain.criteria["relevance"].value
    assert not rated.criteria["relevance"].judged and "could not rate it" in rated.criteria["relevance"].basis


def test_foundation_weight_is_zero_when_the_profile_excludes_foundational_work():
    ctx = RankingContext(topic="neural radiance fields view synthesis", use_profile=True, today=TODAY,
                         profile=PROFILE.model_copy(update={"include_foundational": False}))
    a = SCORER.assess([cand(paper(9))], ctx)[0]
    assert a.criteria["foundation"].value is not None and a.criteria["foundation"].weight == 0


def test_only_confident_judges_can_make_confidence_high():
    ctx = RankingContext(topic="text guided gaussian splatting scene editing", profile=PROFILE, use_profile=True,
                         today=TODAY)
    pid1 = PAPERS[1]["paperId"]
    one = SCORER.assess([cand(paper(1))], ctx, judgments=judged(pid1, "relevance", 90))[0]
    both = judged(pid1, "relevance", 90)
    both[pid1].update(judged(pid1, "usefulness", 85)[pid1])
    two = SCORER.assess([cand(paper(1))], ctx, judgments=both)[0]
    assert one.confidence is Band.medium and two.confidence is Band.high
