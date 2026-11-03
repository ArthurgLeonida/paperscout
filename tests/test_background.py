import json
import re
from pathlib import Path

import pytest

from paperscout import cli, doctor
from paperscout.background import PLACEHOLDER, PROSE_FILES, inspect_background
from paperscout.models import (
    Candidate,
    FeedbackLabel,
    Paper,
    Priority,
    ReadingDepth,
    ReadingRole,
    RecencyPreference,
    ResearchProfile,
)
from paperscout.ranking.scorer import HeuristicScorer, RankingContext, load_ranking_config
from paperscout.service import ServiceError

from .conftest import PAPERS, TODAY, pid, public

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "templates"
SCORER = HeuristicScorer(load_ranking_config())


def paper(n: int) -> Paper:
    return Paper.from_s2(public(PAPERS[n]))


def assess(n: int, topic: str, knowledge=()):
    profile = ResearchProfile(
        name="Background test", field="3D scene editing", keywords=["gaussian splatting"],
        research_question=topic, recency_preference=RecencyPreference.none, knowledge=list(knowledge),
    )
    ctx = RankingContext(topic=topic, profile=profile, use_profile=True, today=TODAY,
                         recency_preference=RecencyPreference.none)
    return SCORER.assess([Candidate(paper=paper(n))], ctx)[0]


# ----------------------------------------------------------------------------- knowledge map (deterministic)


def test_expert_topic_lightens_background_paper_without_changing_priority():
    topic = "gaussian splatting real time radiance field rendering"
    base = assess(3, topic)
    assert base.reading_role is ReadingRole.foundational
    assert base.reading_depth in (ReadingDepth.substantive, ReadingDepth.deep)
    adjusted = assess(3, topic, [{"topic": "gaussian splatting", "level": "expert"}])
    assert adjusted.reading_depth is ReadingDepth.overview
    assert "expert in 'gaussian splatting'" in adjusted.background_note
    assert (adjusted.priority, adjusted.heuristic_score) == (base.priority, base.heuristic_score)
    assert adjusted.effort_minutes[1] < base.effort_minutes[1]


def test_new_topic_deepens_relevant_background_paper():
    topic = "survey of gaussian splatting rendering reconstruction editing"
    base = assess(4, topic)
    assert base.reading_role is ReadingRole.survey and base.priority in (Priority.p1, Priority.p2)
    assert base.reading_depth is ReadingDepth.overview
    adjusted = assess(4, topic, [{"topic": "gaussian splatting", "level": "new"}])
    assert adjusted.reading_depth is ReadingDepth.substantive
    assert "new to you" in adjusted.background_note
    assert adjusted.priority is base.priority


def test_method_on_new_topic_points_to_prerequisites():
    a = assess(1, "text guided gaussian scene editing", [{"topic": "diffusion", "level": "new"}])
    assert a.reading_role is ReadingRole.recent_method
    assert "consider reading its prerequisites first" in a.background_note


def test_unmentioned_or_working_topics_change_nothing():
    topic = "gaussian splatting real time radiance field rendering"
    base = assess(3, topic)
    for knowledge in ([{"topic": "medical imaging", "level": "expert"}], [{"topic": "gaussian splatting", "level": "working"}]):
        a = assess(3, topic, knowledge)
        assert a.reading_depth is base.reading_depth and a.background_note is None


def test_synonym_topics_match():
    a = assess(3, "gaussian splatting real time radiance field rendering", [{"topic": "3DGS", "level": "expert"}])
    assert a.background_note and "'3DGS'" in a.background_note


def test_knowledge_ignored_in_topic_only_mode():
    profile = ResearchProfile(name="p", knowledge=[{"topic": "gaussian splatting", "level": "expert"}])
    ctx = RankingContext(topic="gaussian splatting rendering", profile=profile, use_profile=False, today=TODAY)
    assert SCORER.assess([Candidate(paper=paper(3))], ctx)[0].background_note is None


# ----------------------------------------------------------------------------- templates stay consistent with the code


def filled_template_profile() -> dict:
    text = (TEMPLATES / "profile" / "profile.json").read_text(encoding="utf-8")
    return json.loads(PLACEHOLDER.sub("filled", text))


def test_profile_json_template_matches_the_model():
    profile = ResearchProfile.model_validate(filled_template_profile())
    assert profile.knowledge and profile.knowledge[0].level.value == "familiar"


def test_prose_templates_have_the_sections_setup_relies_on():
    required = {
        "01-researcher-background.md": ["## Knowledge Map", "## Own Publications and Preprints", "## Tools and Methods"],
        "02-research-goals.md": ["## Current Project", "## Interests", "## Seed Papers", "## Saved Searches"],
        "03-reading-style.md": ["## Time Budget", "## How You Read and Learn"],
        "04-recommendation-criteria.md": ["## Reading Roles, Defined for Your Project", "## Calibration From Feedback"],
    }
    assert set(required) == set(PROSE_FILES)
    for name, headings in required.items():
        text = (TEMPLATES / "profile" / name).read_text(encoding="utf-8")
        assert all(h in text for h in headings), name
        assert PLACEHOLDER.search(text), f"{name} should ship with placeholders"


def test_claude_local_template_imports_every_prose_file():
    text = (TEMPLATES / "CLAUDE.local.md").read_text(encoding="utf-8")
    assert sorted(re.findall(r"^@profile/(\S+)$", text, re.M)) == sorted(PROSE_FILES)


# ----------------------------------------------------------------------------- profile sync


def test_sync_rejects_unfilled_template(svc):
    with pytest.raises(ServiceError, match="placeholders"):
        svc.sync_profile(TEMPLATES / "profile" / "profile.json")
    assert svc.store.list_profiles() == []


def test_sync_loads_activates_and_is_idempotent(svc, tmp_path):
    path = tmp_path / "profile.json"
    data = filled_template_profile() | {"name": "My Project"}
    path.write_text(json.dumps(data), encoding="utf-8")
    first = svc.sync_profile(path)
    second = svc.sync_profile(path)
    assert first.id == second.id and svc.store.active_profile().id == first.id
    assert len(svc.store.list_profiles()) == 1
    assert svc.store.get_setting(f"profile_sync:{first.id}")


def test_sync_reports_invalid_and_missing_files(svc, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x", "recency_preference": "sometimes"}), encoding="utf-8")
    with pytest.raises(ServiceError, match="not a valid profile"):
        svc.sync_profile(bad)
    with pytest.raises(ServiceError, match="Run /setup"):
        svc.sync_profile(tmp_path / "missing.json")


def test_profile_sync_cli_default_path(svc, tmp_path, monkeypatch):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.json").write_text(json.dumps({"name": "CLI Project"}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out = cli.run("profile", ["sync"], svc=svc)
    assert code == 0 and "CLI Project" in out and "active profile" in out


# ----------------------------------------------------------------------------- background status


def write_background(root: Path, *, filled: bool = True, local: bool = True) -> None:
    (root / "CLAUDE.md").write_text("# test", encoding="utf-8")
    prof = root / "profile"
    prof.mkdir()
    for name in PROSE_FILES:
        text = (TEMPLATES / "profile" / name).read_text(encoding="utf-8")
        (prof / name).write_text(PLACEHOLDER.sub("filled", text) if filled else text, encoding="utf-8")
    (prof / "profile.json").write_text(json.dumps({"name": "Status Project"}), encoding="utf-8")
    if local:
        (root / "CLAUDE.local.md").write_text((TEMPLATES / "CLAUDE.local.md").read_text(encoding="utf-8"),
                                              encoding="utf-8")


def test_inspect_background_states(tmp_path, monkeypatch):
    monkeypatch.setattr("paperscout.config.project_root", lambda: None)
    assert set(inspect_background(tmp_path)["files"].values()) == {"missing"}
    write_background(tmp_path, filled=False, local=False)
    status = inspect_background(tmp_path)
    assert all("placeholder" in status["files"][n] for n in PROSE_FILES)
    assert status["claude_local_md"] == "missing"


def test_doctor_reports_synced_background(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("paperscout.config.project_root", lambda: None)
    monkeypatch.chdir(tmp_path)
    write_background(tmp_path)
    svc.sync_profile(tmp_path / "profile" / "profile.json")
    text = doctor.render(doctor.run_doctor(svc.settings))
    assert "CLAUDE.local.md: imports profile/" in text
    assert "profile.json: synced with the active profile" in text
    (tmp_path / "profile" / "profile.json").write_text(json.dumps({"name": "Status Project", "field": "x"}),
                                                       encoding="utf-8")
    assert "not synced" in doctor.render(doctor.run_doctor(svc.settings))


# ----------------------------------------------------------------------------- reading history import


def test_import_mark_already_known_is_idempotent_and_excludes_from_discovery(svc, editing_profile, tmp_path):
    svc.create_folder("Reading history", "papers already read")
    path = tmp_path / "read.txt"
    path.write_text(f"{pid(1)}\n{pid(2)}\n", encoding="utf-8")
    preview = svc.import_file(path, "Reading history", dry_run=True, mark="already_known")
    assert preview["marked"]["papers"] == 0 and svc.store.feedback_history(pid(1)) == []
    result = svc.import_file(path, "Reading history", mark="already-known")
    assert result["marked"] == {"label": "already_known", "papers": 2}
    again = svc.import_file(path, "Reading history", mark="already_known")
    assert again["marked"]["papers"] == 0
    assert [e.label for e in svc.store.feedback_history(pid(1))] == [FeedbackLabel.already_known]
    report = svc.recent("text-guided gaussian scene editing", days=120)
    assert {pid(1), pid(2)}.isdisjoint({i.paper.paper_id for i in report.items})


def test_import_rejects_unknown_mark(svc, tmp_path):
    svc.create_folder("Reading history", "papers already read")
    path = tmp_path / "read.txt"
    path.write_text(f"{pid(1)}\n", encoding="utf-8")
    with pytest.raises(ServiceError, match="Valid labels"):
        svc.import_file(path, "Reading history", mark="read-it")


# ----------------------------------------------------------------------------- feedback log & cache hygiene


def test_feedback_log_lists_newest_first_with_titles(svc):
    svc.record_feedback(pid(1), "relevant")
    svc.record_feedback(pid(5), "not-useful", "animation, not editing")
    code, out = cli.run("feedback-log", [], svc=svc)
    lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert code == 0 and "not_useful" in lines[0] and "Character Animation" in lines[0]
    assert "relevant" in lines[1]


def test_expired_cache_rows_are_purged_on_close(settings, fake_s2):
    import httpx

    from paperscout.s2.client import SemanticScholarClient
    from paperscout.service import Paperscout
    from paperscout.store.db import Store, connect

    store = Store(connect(settings.db_path))
    svc = Paperscout(settings, store, SemanticScholarClient(settings, cache=store, transport=httpx.MockTransport(fake_s2)))
    store.cache_put("old", "u", "{}", ttl_seconds=-10)
    store.cache_put("fresh", "u", "{}", ttl_seconds=3600)
    svc.close()
    reopened = Store(connect(settings.db_path))
    keys = [r[0] for r in reopened.conn.execute("SELECT key FROM http_cache")]
    assert keys == ["fresh"]
