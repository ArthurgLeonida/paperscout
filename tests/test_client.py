import logging
from dataclasses import replace

import httpx
import pytest

from paperscout.s2 import client as client_mod
from paperscout.http import ApiNotFound, ApiRateLimited, parse_retry_after
from paperscout.s2.client import SemanticScholarClient, search_query_text
from paperscout.store.db import Store, connect

from .conftest import pid


def make_client(settings, handler, sleeps, cache=None, **overrides):
    return SemanticScholarClient(replace(settings, **overrides), cache=cache,
                                 transport=httpx.MockTransport(handler), sleep=sleeps.append)


def test_retry_after_is_honoured_then_succeeds(settings, fake_s2, sleeps):
    fake_s2.fail_queue = [httpx.Response(429, headers={"Retry-After": "7"}, json={"message": "Too Many Requests"})]
    client = make_client(settings, fake_s2, sleeps)
    page = client.search("gaussian splatting")
    assert page["data"]
    assert sleeps == [7.0]
    assert client.network_calls == 2


def test_backoff_without_retry_after(settings, fake_s2, sleeps):
    fake_s2.fail_queue = [httpx.Response(429, json={"message": "Too Many Requests", "code": "429"}),
                          httpx.Response(503, text="unavailable")]
    client = make_client(settings, fake_s2, sleeps)
    client.search("gaussian")
    assert len(sleeps) == 2
    assert 2.0 <= sleeps[0] <= 2.5 and 4.0 <= sleeps[1] <= 4.5  # exponential with jitter


def test_rate_limit_exhausted_raises_clear_error(settings, sleeps):
    handler = lambda request: httpx.Response(429, json={"message": "Too Many Requests"})  # noqa: E731
    client = make_client(settings, handler, sleeps)
    with pytest.raises(ApiRateLimited, match="S2_API_KEY"):
        client.search("anything")
    assert len(sleeps) == settings.s2_max_retries


def test_retry_after_beyond_cap_fails_fast(settings, sleeps):
    handler = lambda request: httpx.Response(429, headers={"Retry-After": "600"})  # noqa: E731
    client = make_client(settings, handler, sleeps)
    with pytest.raises(ApiRateLimited, match="600s"):
        client.search("anything")
    assert sleeps == []


def test_parse_retry_after_formats():
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480 - 30) == pytest.approx(30, abs=1)


def test_transport_errors_are_retried(settings, fake_s2, sleeps):
    state = {"n": 0}

    def flaky(request):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectTimeout("boom", request=request)
        return fake_s2(request)

    client = make_client(settings, flaky, sleeps)
    assert client.get_paper(pid(1)).paper_id == pid(1)
    assert len(sleeps) == 1


def test_not_found_and_title_match_404(settings, fake_s2, sleeps):
    client = make_client(settings, fake_s2, sleeps)
    with pytest.raises(ApiNotFound):
        client.get_paper("DOI:10.9999/missing")
    assert client.match_title("no such paper title anywhere") is None


def test_hyphenated_queries_are_sanitised(settings, fake_s2, sleeps):
    assert search_query_text("text-guided 3D scene‐editing") == "text guided 3D scene editing"
    client = make_client(settings, fake_s2, sleeps)
    results = client.search_papers("text-guided gaussian editing", max_results=5)
    assert results, "hyphens must be removed before calling /paper/search"


def test_cache_hits_and_expiry(settings, fake_s2, sleeps, tmp_path):
    store = Store(connect(tmp_path / "c.sqlite3"))
    client = make_client(settings, fake_s2, sleeps, cache=store)
    client.search("gaussian")
    client.search("gaussian")
    assert fake_s2.count("/paper/search") == 1 and client.cache_hits == 1
    client.search("gaussian", refresh=True)
    assert fake_s2.count("/paper/search") == 2
    store.conn.execute("UPDATE http_cache SET expires_at = '2000-01-01T00:00:00+00:00'")
    client.search("gaussian")
    assert fake_s2.count("/paper/search") == 3


def test_batch_is_chunked_and_aligned(settings, fake_s2, sleeps, monkeypatch):
    monkeypatch.setattr(client_mod, "BATCH_LIMIT", 2)
    client = make_client(settings, fake_s2, sleeps)
    papers = client.get_papers([pid(1), "DOI:10.9999/missing", "ARXIV:2609.00001"])
    assert [p.paper_id if p else None for p in papers] == [pid(1), None, pid(1)]
    assert fake_s2.count("/paper/batch") == 2


def test_api_key_sent_but_never_logged(settings, fake_s2, sleeps, caplog):
    secret = "sk-test-SECRET-123"
    client = make_client(settings, fake_s2, sleeps, s2_api_key=secret)
    with caplog.at_level(logging.DEBUG, logger="paperscout"):
        client.search("gaussian")
    assert fake_s2.calls[-1].headers["x-api-key"] == secret
    assert secret not in caplog.text
    redacted = replace(settings, s2_api_key=secret).redacted()
    assert redacted["s2_api_key"] == "set" and secret not in str(redacted)


def test_pacing_waits_between_requests(settings, fake_s2, sleeps):
    clock = iter([0.0, 0.2, 0.2])  # 1st request stamps 0.0; 2nd paces at 0.2 then stamps
    client = SemanticScholarClient(replace(settings, s2_min_interval=1.0), transport=httpx.MockTransport(fake_s2),
                                   sleep=sleeps.append, monotonic=lambda: next(clock))
    client.get_paper(pid(1))
    client.get_paper(pid(2))
    assert sleeps and sleeps[0] == pytest.approx(0.8)
