from datetime import date

import pytest

from paperscout.capabilities import UnsupportedCapability
from paperscout.models import FeedbackLabel, Priority, ResearchProfile
from paperscout.service import ServiceError

from .conftest import pid


def titles(report):
    return [i.paper.title.replace("[SYNTHETIC] ", "") for i in report.items]


# ----------------------------------------------------------------------------- recent / discover


def test_recent_shows_window_and_flags_dates(svc, editing_profile):
    report = svc.recent("text-guided gaussian scene editing", days=120)
    assert report.date_window["since"] == "2026-07-04" and report.date_window["until"] == "2026-11-01"
    assert "indexing date is not used" in report.date_window["basis"]
    shown = {i.paper.paper_id for i in report.items}
    assert pid(3) not in shown and pid(9) not in shown  # old papers are outside the window
    assert any("only a publication year" in n for n in report.notices)  # paper 7 (year only)
    assert report.profile_mode == "topic + profile"
    assert report.run_id and report.stats["retrieved"] >= len(report.items)


def test_recent_excludes_papers_with_unknown_dates_explicitly(svc, editing_profile, fake_s2):
    report = svc.recent("gaussian scene editing", days=3650)
    # paper 11 has no date: the fake API (like the real filter) never returns it in a date-filtered search
    assert pid(11) not in {i.paper.paper_id for i in report.items}
    window_check = svc._apply_window([svc_candidate(11)], (date(2026, 1, 1), date(2026, 11, 1)))
    assert window_check[1][0].reason.startswith("publication date unknown")


def svc_candidate(n):
    from paperscout.models import Candidate, Paper

    from .conftest import PAPERS, public

    return Candidate(paper=Paper.from_s2(public(PAPERS[n])))


def test_reviewed_papers_are_excluded_unless_requested(svc, editing_profile):
    svc.record_feedback(pid(1), "understood")
    report = svc.recent("text-guided gaussian scene editing", days=120)
    assert pid(1) not in {i.paper.paper_id for i in report.items}
    assert any(e.paper.paper_id == pid(1) and "understood" in e.reason for e in report.excluded)
    again = svc.recent("text-guided gaussian scene editing", days=120, include_read=True)
    item = next(i for i in again.items if i.paper.paper_id == pid(1))
    assert item.latest_feedback is FeedbackLabel.understood


def test_read_later_is_not_treated_as_reviewed(svc, editing_profile):
    svc.record_feedback(pid(1), "read-later")
    report = svc.recent("text-guided gaussian scene editing", days=120)
    assert pid(1) in {i.paper.paper_id for i in report.items}


def test_provenance_and_dedup_across_search_and_recommendations(svc, editing_profile):
    svc.record_feedback(pid(2), "relevant")  # becomes a positive seed -> recommendations are used
    report = svc.recent("gaussian scene editing", days=120, include_read=True)
    item = next(i for i in report.items if i.paper.paper_id == pid(1))
    sources = {p.source for p in item.provenance}
    assert {"s2.search", "s2.recommendations"} <= sources
    assert report.stats["duplicates_merged"] >= 1


def test_tldr_used_when_abstract_missing(svc, editing_profile):
    report = svc.recent("instruction driven scene editing", days=120)
    item = next(i for i in report.items if i.paper.paper_id == pid(6))
    assert item.assessment.evidence_level.value == "tldr"


def test_topic_mismatch_uses_topic_only(svc, editing_profile):
    report = svc.recent("medical image segmentation", days=120)
    assert report.profile_mode.startswith("topic-only")
    assert any("differs materially" in n for n in report.notices)
    forced = svc.recent("medical image segmentation", days=120, profile="3D Editing")
    assert "low overlap" in forced.profile_mode


def test_no_profile_is_stated(svc):
    report = svc.recent("gaussian scene editing", days=120)
    assert report.profile_mode == "topic-only (no profile)"
    assert any("No research profile" in n for n in report.notices)


def test_empty_results(svc, editing_profile):
    report = svc.recent("zzzz qqqq", days=30)
    assert report.items == [] and any("No candidates" in n for n in report.notices)


def test_conflicting_window_options(svc):
    with pytest.raises(ServiceError, match="either --days or --since"):
        svc.recent("x", days=10, since=date(2026, 1, 1))
    with pytest.raises(ServiceError, match="after --until"):
        svc.recent("x", since=date(2026, 5, 1), until=date(2026, 1, 1))


def test_discover_adds_prerequisites_and_balances_roles(svc, editing_profile):
    report = svc.discover("text-guided gaussian scene editing background", limit=6)
    roles = {i.assessment.reading_role.value for i in report.items}
    assert roles & {"foundational", "prerequisite"}
    prereq = [i for i in report.items if any(p.source == "s2.references" for p in i.provenance)]
    assert prereq, "papers cited by several top results should be surfaced"


def test_cached_results_and_reranking_after_feedback(svc, editing_profile, fake_s2):
    first = svc.recent("text-guided gaussian scene editing", days=120)
    searches = fake_s2.count("/paper/search")
    svc.record_feedback(first.items[0].paper.paper_id, "irrelevant", "wrong scope")
    second = svc.recent("text-guided gaussian scene editing", days=120)
    assert fake_s2.count("/paper/search") == searches  # served from cache
    assert first.items[0].paper.paper_id not in {i.paper.paper_id for i in second.items}
    assert all(i.seen_before for i in second.items if i.paper.paper_id in {x.paper.paper_id for x in first.items})


# ----------------------------------------------------------------------------- folders & ranking


def make_folder(svc, name, *include, profile=None):
    folder, _ = svc.create_folder(name, f"{name} papers", list(include), profile_name=profile)
    return folder


def test_rank_folder_filters(svc, editing_profile):
    folder = make_folder(svc, "3D Scene Editing", "gaussian splatting scene editing")
    for n in (1, 2, 3):
        svc.add_to_folder(pid(n), "3D Scene Editing")
    svc.record_feedback(pid(3), "understood")
    report = svc.rank_folder("3d scene editing", unread=True)
    assert {i.paper.paper_id for i in report.items} == {pid(1), pid(2)}
    assert "not a Semantic Scholar website folder" in report.source
    # --new: nothing added since the last ranking
    assert svc.rank_folder(folder.id, new=True).items == []
    svc.add_to_folder(pid(7), "3D Scene Editing")
    assert [i.paper.paper_id for i in svc.rank_folder(folder.id, new=True).items] == [pid(7)]


def test_rank_empty_folder_explains(svc, editing_profile):
    make_folder(svc, "Empty", "gaussian")
    report = svc.rank_folder("Empty")
    assert report.items == [] and any("No papers" in n for n in report.notices)


def test_unsupported_rank_sources_fail_clearly(svc):
    with pytest.raises(UnsupportedCapability, match="Research Feeds"):
        svc.rank_all(source="s2-feed")
    with pytest.raises(UnsupportedCapability, match="Library folders"):
        svc.rank_all(source="s2-library")
    with pytest.raises(UnsupportedCapability, match="phase-3"):
        svc.rank_all(source="zotero")


def test_rank_all_dedups_papers_in_multiple_folders(svc, editing_profile):
    make_folder(svc, "Editing", "scene editing")
    make_folder(svc, "Gaussian Splatting", "gaussian splatting rendering")
    svc.add_to_folder(pid(1), "Editing")
    svc.add_to_folder(pid(1), "Gaussian Splatting")
    svc.add_to_folder(pid(3), "Gaussian Splatting")
    report = svc.rank_all()
    assert report.profile_mode == "profile (shared goal)"
    ids = [i.paper.paper_id for i in report.items]
    assert sorted(ids) == sorted({pid(1), pid(3)})
    item = next(i for i in report.items if i.paper.paper_id == pid(1))
    assert set(item.per_folder) == {"Editing", "Gaussian Splatting"}
    assert report.stats["duplicates_across_folders"] == 1


def test_rank_all_without_shared_goal_returns_sections(svc, editing_profile):
    other = svc.save_profile(ResearchProfile(name="Medical", field="medical imaging",
                                             research_question="medical image segmentation"))
    make_folder(svc, "Editing", "scene editing", profile="3D Editing")
    make_folder(svc, "Segmentation", "medical segmentation", profile=other.name)
    svc.add_to_folder(pid(1), "Editing")
    svc.add_to_folder(pid(8), "Segmentation")
    report = svc.rank_all()
    assert report.items == [] and len(report.sections) == 2
    assert any("no shared goal" in n for n in report.notices)
    forced = svc.rank_all(profile="3D Editing")
    assert forced.items and forced.profile_name == "3D Editing"


def test_rank_all_no_folders(svc):
    report = svc.rank_all()
    assert any("no local research folders" in n for n in report.notices)


# ----------------------------------------------------------------------------- add / classify


def test_add_requires_existing_folder_and_never_creates(svc):
    with pytest.raises(ServiceError, match="never created implicitly"):
        svc.add_to_folder(pid(1), "Nope")
    assert svc.store.list_folders() == []


def test_add_dry_run_then_apply_is_idempotent_and_audited(svc):
    folder = make_folder(svc, "Editing", "scene editing")
    preview = svc.add_to_folder(pid(1), "Editing", dry_run=True)
    assert preview["status"] == "preview" and svc.store.memberships(folder.id) == []
    assert svc.add_to_folder(pid(1), "Editing")["status"] == "added"
    assert svc.add_to_folder("arXiv:2609.00001", "Editing")["status"] == "unchanged"
    outcomes = [e["outcome"] for e in svc.store.audit_entries()]
    assert "previewed" in outcomes and "added" in outcomes and "already present (no change)" in outcomes


def test_external_destinations_are_unsupported(svc):
    make_folder(svc, "Editing", "scene editing")
    with pytest.raises(UnsupportedCapability):
        svc.add_to_folder(pid(1), "Editing", destination="zotero")
    with pytest.raises(UnsupportedCapability, match="no official endpoint"):
        svc.add_to_folder(pid(1), "Editing", destination="s2-library")


def test_ambiguous_title_lookup_returns_options(svc):
    res = svc.resolve("Text-Guided Editing of 3D Gaussian Splatting Scenes")
    assert res.status == "ambiguous"
    assert {p.paper_id for p in res.options} == {pid(1), pid(10)}
    with pytest.raises(ServiceError, match="Options"):
        svc.require_paper("Text-Guided Editing of 3D Gaussian Splatting Scenes")


def test_unique_title_resolves(svc):
    res = svc.resolve("[SYNTHETIC] Medical Image Segmentation with Foundation Models")
    assert res.status == "resolved" and res.paper.paper_id == pid(8)


def test_classify_recommends_without_mutation(svc, editing_profile):
    from paperscout.taxonomy import classify

    make_folder(svc, "Scene Editing", "text guided scene editing", "gaussian splatting editing")
    make_folder(svc, "Medical", "medical image segmentation")
    svc.add_to_folder(pid(2), "Scene Editing")
    result = classify(svc, pid(1))
    assert result.suggestions and result.suggestions[0].folder == "Scene Editing"
    assert result.new_folder_warranted == "no"
    assert result.mutated is False
    assert result.suggested_commands == [f'/add {pid(1)} --folder "Scene Editing"']
    assert len(svc.store.memberships(svc.store.get_folder("Scene Editing").id)) == 1


def test_classify_suggests_new_folder_when_nothing_fits(svc, editing_profile):
    from paperscout.taxonomy import classify

    make_folder(svc, "Medical", "medical image segmentation")
    result = classify(svc, pid(1))
    assert result.suggestions == [] and result.new_folder_warranted == "yes-possibly"


# ----------------------------------------------------------------------------- feedback


def test_feedback_is_append_only(svc, editing_profile):
    svc.record_feedback(pid(1), "relevant")
    _, _, history = svc.record_feedback(pid(1), "not-useful", "only tangential")
    assert [h.label.value for h in history] == ["relevant", "not_useful"]
    assert history[1].reason == "only tangential"


def test_invalid_feedback_label(svc):
    with pytest.raises(ServiceError, match="Valid labels"):
        svc.record_feedback(pid(1), "meh")


def test_relevance_feedback_is_profile_scoped_but_read_state_is_global(svc, editing_profile):
    svc.record_feedback(pid(1), "irrelevant")
    svc.record_feedback(pid(2), "understood")
    other = svc.save_profile(ResearchProfile(name="Other", field="x"))
    latest = svc.store.latest_feedback([pid(1), pid(2)], other.id)
    assert pid(1) not in latest and latest[pid(2)].label is FeedbackLabel.understood


# ----------------------------------------------------------------------------- Claude assessments


def base_assessment(**changes):
    data = {
        "paper_id": pid(1), "model": "claude-opus-5-5", "prompt_version": "assess-v1", "evidence_level": "abstract",
        "topic_relevance": "high", "project_usefulness": "medium", "reading_role": "recent_method",
        "priority": "P1", "reading_depth": "substantive", "rationale": "Directly addresses text-guided editing.",
        "uncertainties": ["Abstract only"], "next_action": "Read the method section.",
    }
    data.update(changes)
    return data


def test_valid_claude_assessment_is_stored(svc, editing_profile):
    svc.require_paper(pid(1))
    stored = svc.record_claude_assessment(base_assessment())
    assert stored.quality_judgments["reproducibility"] == "unknown"
    assert svc.store.assessments_for(pid(1))[0]["assessor"] == "claude:claude-opus-5-5"


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"quality_judgments": {"reproducibility": "good"}}, "require full-text evidence"),
        ({"uncertainties": []}, "uncertainties"),
        ({"priority": "P0"}, "priority"),
        ({"evidence_level": "full_text"}, "full_text_version"),
        ({"quality_judgments": {"novelty": "high"}}, "unknown quality_judgments"),
        ({"rationale": "ok"}, "rationale"),
    ],
)
def test_invalid_model_output_is_rejected(svc, changes, message):
    svc.require_paper(pid(1))
    with pytest.raises(ServiceError, match=message):
        svc.record_claude_assessment(base_assessment(**changes))
    assert svc.store.assessments_for(pid(1)) == []


def test_assessment_cannot_claim_more_evidence_than_stored(svc):
    svc.require_paper(pid(6))  # no abstract
    with pytest.raises(ServiceError, match="only"):
        svc.record_claude_assessment(base_assessment(paper_id=pid(6)))


def test_assess_single_paper(svc, editing_profile):
    report = svc.assess(pid(1))
    assert len(report.items) == 1 and report.items[0].assessment.priority in (Priority.p1, Priority.p2)


def test_assess_without_goal_reports_unknown_priority(svc):
    report = svc.assess(pid(1))
    a = report.items[0].assessment
    assert a.priority is Priority.unknown
    assert a.topic_relevance.value is None and a.project_usefulness.value is None
