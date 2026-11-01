
from paperscout.dedup import deduplicate
from paperscout.models import (
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
    assert CONFIG.version == "heuristic-v1"


def test_missing_abstract_is_metadata_evidence_with_low_confidence():
    a = assess([cand(paper(1, abstract=None, tldr=None))])[0]
    assert a.evidence_level is EvidenceLevel.metadata
    assert a.confidence is Band.low
    assert any("title/metadata only" in u for u in a.uncertainties)


def test_unknown_date_is_unknown_not_zero():
    a = assess([cand(paper(11))])[0]
    assert a.recency.value is None and a.recency.band is Band.unknown
    assert a.heuristic_score is not None  # renormalised over known components
    assert any("date unknown" in u for u in a.uncertainties)


def test_year_only_date_is_flagged():
    a = assess([cand(paper(7))])[0]
    assert "year is known" in a.recency.basis
    assert any("year only" in u for u in a.uncertainties)


def test_recent_low_citation_paper_not_penalised_vs_cited_older():
    recent, old = assess([cand(paper(1)), cand(paper(3))])
    assert recent.heuristic_score > old.heuristic_score
    assert any("not penalised" in u for u in recent.uncertainties)


def test_citations_only_label_roles():
    old = assess([cand(paper(9))], topic="neural radiance fields view synthesis")[0]
    assert old.reading_role is ReadingRole.foundational
    assert "do not raise priority" in old.role_basis


def test_survey_role_and_overview_depth():
    a = assess([cand(paper(4))])[0]
    assert a.reading_role is ReadingRole.survey
    assert a.reading_depth.value in ("overview", "skip")


def test_exclusion_criteria_penalise_and_flag():
    plain = assess([cand(paper(5))])[0]
    excluded = assess([cand(paper(5))], exclusion_criteria=["character animation"])[0]
    assert excluded.exclusion_hits == ["character animation"]
    assert excluded.heuristic_score < plain.heuristic_score


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
