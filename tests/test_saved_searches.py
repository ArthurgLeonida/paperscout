import pytest

from paperscout import cli
from paperscout.http import ApiError
from paperscout.models import MAX_SAVED_SEARCHES, ResearchProfile
from paperscout.service import ServiceError

SAVED = ["gaussian scene editing", "multi-view consistent editing", "medical volume segmentation"]


@pytest.fixture
def saved_profile(svc):
    profile = ResearchProfile(
        name="Saved Searches", field="3D scene editing", research_question="How to edit 3D scenes consistently?",
        keywords=["gaussian splatting", "scene editing"], search_queries=SAVED,
    )
    return svc.save_profile(profile, activate=True)


def test_model_cleans_and_limits_saved_searches():
    p = ResearchProfile(name="x", search_queries=["  3D   editing ", "3d editing", "", "score distillation"])
    assert p.search_queries == ["3D editing", "score distillation"]
    with pytest.raises(ValueError, match=f"at most {MAX_SAVED_SEARCHES}"):
        ResearchProfile(name="x", search_queries=[f"query {i}" for i in range(MAX_SAVED_SEARCHES + 1)])
    with pytest.raises(ValueError, match="too long"):
        ResearchProfile(name="x", search_queries=["word " * 30])


def test_no_topic_runs_every_saved_search_on_arxiv(svc, saved_profile, fake_arxiv, fake_s2):
    svc.provider = "arxiv"
    report = svc.discover(None, limit=8)
    assert len(fake_arxiv.queries) == len(SAVED)  # one request per search; no widening
    assert all(" OR " not in q for q in fake_arxiv.queries)
    assert "saved searches" in report.heading and report.profile_mode == "saved searches + profile"
    assert any("ran the 3 saved searches" in n for n in report.notices)
    found_by = {p.query for item in report.items for p in item.provenance}
    assert {"gaussian scene editing", "multi-view consistent editing"} <= found_by
    assert fake_s2.calls == []


def test_no_topic_runs_saved_searches_on_semantic_scholar(svc, saved_profile, fake_s2):
    report = svc.recent(None, days=120)
    searches = [c for c in fake_s2.calls if c.url.path.endswith("/paper/search")]
    assert len(searches) == len(SAVED)
    assert report.date_window and report.items


def test_one_failing_search_does_not_sink_the_others(svc, saved_profile, monkeypatch):
    svc.provider = "arxiv"
    real = svc.arxiv.search_papers

    def flaky(text, **kwargs):
        if text == "multi-view consistent editing":
            raise ApiError("arXiv HTTP 503: maintenance")
        return real(text, **kwargs)

    monkeypatch.setattr(svc.arxiv, "search_papers", flaky)
    report = svc.discover(None, limit=5)
    assert report.items
    assert any("Saved search 'multi-view consistent editing' was skipped" in n for n in report.notices)


def test_all_saved_searches_failing_is_an_error(svc, saved_profile, monkeypatch):
    svc.provider = "arxiv"

    def broken(text, **kwargs):
        raise ApiError("arXiv HTTP 503: maintenance")

    monkeypatch.setattr(svc.arxiv, "search_papers", broken)
    with pytest.raises(ServiceError, match="All 3 saved searches failed"):
        svc.discover(None)


def test_without_saved_searches_the_research_question_is_used_and_flagged(svc, editing_profile):
    report = svc.discover(None, limit=5)
    assert any("has no saved searches" in n and "/setup --section searches" in n for n in report.notices)


def test_cli_accepts_no_topic_only_with_a_profile(svc, saved_profile):
    svc.provider = "arxiv"
    code, out = cli.run("discover", [], svc=svc)
    assert code == 0 and "saved searches" in out


def test_cli_rejects_no_topic_without_a_profile(svc):
    code, out = cli.run("discover", [], svc=svc)
    assert code == 2 and "/setup" in out
