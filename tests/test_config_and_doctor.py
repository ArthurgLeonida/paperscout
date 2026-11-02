import json
from dataclasses import replace

import httpx
import pytest

from paperscout import cli, doctor
from paperscout.config import load_dotenv, load_settings
from paperscout.s2.client import S2BudgetExceeded, S2RateLimited, SemanticScholarClient

from .conftest import pid

FAKE_KEY = "fake-test-key-0123456789"


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("S2_API_KEY", "PAPERSCOUT_HOME", "PAPERSCOUT_S2_MAX_RETRIES", "PAPERSCOUT_S2_MAX_REQUESTS",
                 "PAPERSCOUT_RECENT_DAYS", "PAPERSCOUT_TEST_VALUE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("paperscout.config.project_root", lambda: None)
    return monkeypatch


# ----------------------------------------------------------------------------- .env loading


def test_dotenv_parsing_rules(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text(
        "﻿# comment line\n"
        "PAPERSCOUT_TEST_VALUE=\"quoted value\"\n"
        "export PAPERSCOUT_RECENT_DAYS=45 # trailing comment\n"
        "S2_API_KEY=\n"
        "not a key value line\n",
        encoding="utf-8",
    )
    loaded = load_dotenv(env)
    import os

    assert os.environ["PAPERSCOUT_TEST_VALUE"] == "quoted value"
    assert os.environ["PAPERSCOUT_RECENT_DAYS"] == "45"
    assert "S2_API_KEY" not in os.environ  # empty values are ignored
    assert sorted(loaded) == ["PAPERSCOUT_RECENT_DAYS", "PAPERSCOUT_TEST_VALUE"]


def test_dotenv_never_overrides_real_environment(tmp_path, clean_env):
    clean_env.setenv("PAPERSCOUT_RECENT_DAYS", "10")
    (tmp_path / ".env").write_text("PAPERSCOUT_RECENT_DAYS=99\n", encoding="utf-8")
    assert load_dotenv(tmp_path / ".env") == []
    assert load_settings(tmp_path).recent_days == 10


def test_settings_pick_up_key_from_dotenv_without_exposing_it(tmp_path, clean_env):
    (tmp_path / ".env").write_text(f"S2_API_KEY={FAKE_KEY}\nPAPERSCOUT_HOME={tmp_path / 'h'}\n", encoding="utf-8")
    settings = load_settings(tmp_path)
    assert settings.s2_api_key == FAKE_KEY
    assert settings.key_source.startswith(".env: ")
    assert settings.s2_max_retries == 3  # keyed default
    assert FAKE_KEY not in json.dumps(settings.redacted())
    assert settings.home == (tmp_path / "h").resolve()


def test_missing_key_defaults_to_fail_fast(tmp_path, clean_env):
    settings = load_settings(tmp_path)
    assert settings.s2_api_key is None and settings.key_source == "not set"
    assert settings.s2_max_retries == 2 and settings.env_files_loaded == ()


def test_project_root_env_is_used_when_cwd_has_none(tmp_path, clean_env):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".env").write_text(f"S2_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    clean_env.setattr("paperscout.config.project_root", lambda: root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    settings = load_settings(elsewhere)
    assert settings.s2_api_key == FAKE_KEY and str(root / ".env") in settings.env_files_loaded


# ----------------------------------------------------------------------------- budget & diagnostics


def test_request_budget_is_a_hard_cap(settings, sleeps):
    handler = lambda request: httpx.Response(429, json={"message": "Too Many Requests"})  # noqa: E731
    client = SemanticScholarClient(replace(settings, s2_max_retries=10, s2_max_requests=3),
                                   transport=httpx.MockTransport(handler), sleep=sleeps.append)
    with pytest.raises(S2BudgetExceeded, match="budget of 3"):
        client.search("anything")
    assert client.network_calls == 3


def test_rate_limit_message_has_sanitized_diagnostics(settings, sleeps):
    def handler(request):
        return httpx.Response(429, headers={"x-amzn-ErrorType": "TooManyRequestsException",
                                            "x-amzn-RequestId": "req-123"}, json={"message": "Too Many Requests"})

    client = SemanticScholarClient(replace(settings, s2_max_retries=1), transport=httpx.MockTransport(handler),
                                   sleep=sleeps.append)
    with pytest.raises(S2RateLimited) as info:
        client.search("gaussian")
    message = str(info.value)
    for fragment in ("HTTP 429", "TooManyRequestsException", "/graph/v1/paper/search", "2 attempt(s)",
                     "Retry-After: absent", "req-123", "API key: not configured", "Nothing further was sent"):
        assert fragment in message
    keyed = SemanticScholarClient(replace(settings, s2_max_retries=0, s2_api_key=FAKE_KEY),
                                  transport=httpx.MockTransport(handler), sleep=sleeps.append)
    with pytest.raises(S2RateLimited) as info:
        keyed.search("gaussian")
    assert "API key: configured" in str(info.value) and FAKE_KEY not in str(info.value)


# ----------------------------------------------------------------------------- doctor


def test_doctor_does_not_create_database_or_print_key(settings):
    keyed = replace(settings, s2_api_key=FAKE_KEY, key_source=".env: X")
    report = doctor.run_doctor(keyed)
    text = doctor.render(report)
    assert report["database"]["exists"] is False and not settings.db_path.exists()
    assert "value not shown" in text and FAKE_KEY not in text and FAKE_KEY not in json.dumps(report, default=str)


def test_doctor_reads_existing_database(svc, editing_profile):
    report = doctor.run_doctor(svc.settings)
    assert report["database"]["counts"]["profiles"] == 1
    assert report["database"]["active_profile"] == "3D Editing"
    assert "S2_API_KEY: not configured" in doctor.render(report)


def test_doctor_live_probe_is_single_request(settings, fake_s2):
    fake_s2.fail_queue = [httpx.Response(429, headers={"x-amzn-ErrorType": "TooManyRequestsException"})]
    report = doctor.run_doctor(settings, live=True, transport=httpx.MockTransport(fake_s2))
    assert report["live_probe"]["ok"] is False and len(fake_s2.calls) == 1
    assert report["live_probe"]["response"]["error_type"] == "TooManyRequestsException"


# ----------------------------------------------------------------------------- prechecks & ranking basis


def test_unsupported_rank_source_does_not_create_database(settings, monkeypatch):
    monkeypatch.setattr("paperscout.service.load_settings", lambda: settings)
    code, out = cli.run("rank", ["--all", "--source", "s2-feed"])
    assert code == 3 and not settings.db_path.exists()


def test_report_states_ranking_basis_and_shows_stored_claude_assessment(svc, editing_profile):
    svc.require_paper(pid(1))
    svc.record_claude_assessment({
        "paper_id": pid(1), "model": "claude-test", "prompt_version": "assess-v1", "evidence_level": "abstract",
        "topic_relevance": "high", "project_usefulness": "high", "reading_role": "recent_method", "priority": "P1",
        "reading_depth": "substantive", "rationale": "Synthetic test rationale for display.",
        "uncertainties": ["abstract only"], "next_action": "read method",
    })
    code, out = cli.run("assess", [pid(1)], svc=svc)
    assert code == 0
    assert "deterministic Python heuristic scores (no LLM involved)" in out
    assert "Stored Claude assessment (claude:claude-test, assess-v1" in out and "not used for ordering" in out
