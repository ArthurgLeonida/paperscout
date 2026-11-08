"""OPENALEX_API_KEY: sent to OpenAlex, never cached, logged or shown; and the daily allowance it reports."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from paperscout import doctor
from paperscout.config import load_settings
from paperscout.http import ApiError
from paperscout.openalex.client import OpenAlexClient
from paperscout.store.db import Store, connect

SECRET = "oa-synthetic-key-123456789"
ALLOWANCE = {"X-RateLimit-Remaining-USD": "0.85", "X-RateLimit-Limit-USD": "1.0", "X-RateLimit-Reset": "7200"}


class Recorder:
    def __init__(self, status=200, body=None, headers=None):
        self.calls: list[httpx.Request] = []
        self.status, self.body, self.headers = status, body, headers or ALLOWANCE

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        body = self.body if self.body is not None else {"meta": {"count": 0}, "results": []}
        if self.status != 200:  # an error that echoes the request URL, key included
            body = {"error": f"bad filter in {request.url}"}
        return httpx.Response(self.status, json=body, headers=self.headers)


def client(settings, transport, cache=None, key=SECRET):
    return OpenAlexClient(replace(settings, openalex=True, openalex_api_key=key), cache=cache,
                          transport=httpx.MockTransport(transport), sleep=lambda s: None)


def test_key_is_sent_but_never_cached_or_stored(settings):
    store = Store(connect(settings.db_path))
    fake = Recorder()
    oa = client(settings, fake, cache=store)
    oa.institution("SIT")
    oa.institution("SIT")  # served from the cache
    assert len(fake.calls) == 1 and fake.calls[0].url.params["api_key"] == SECRET
    rows = store.conn.execute("SELECT * FROM http_cache").fetchall()
    assert rows and all(SECRET not in str(value) for row in rows for value in tuple(row))
    # The same request without a key hits the same cache entry: the key isn't part of the cache key.
    keyless = client(settings, Recorder(), cache=store, key=None)
    keyless.institution("SIT")
    assert keyless.network_calls == 0 and keyless.cache_hits == 1


def test_errors_never_show_the_key(settings):
    oa = client(settings, Recorder(status=400))
    with pytest.raises(ApiError) as caught:
        oa.institution("SIT")
    assert SECRET not in str(caught.value) and "***" in str(caught.value)


def test_no_key_means_no_api_key_parameter(settings):
    fake = Recorder()
    client(settings, fake, key=None).institution("SIT")
    assert "api_key" not in fake.calls[0].url.params


def test_allowance_is_read_from_the_headers(settings):
    oa = client(settings, Recorder())
    assert oa.allowance_text() is None
    oa.institution("SIT")
    assert oa.allowance_text() == ("OpenAlex allowance today: $0.850 of $1.00 left (85%; your API key; resets in "
                                   "about 2 h).")
    low = client(settings, Recorder(headers={**ALLOWANCE, "X-RateLimit-Remaining-USD": "0.01",
                                             "X-RateLimit-Limit-USD": "0.1"}), key=None)
    low.institution("SIT")
    assert "keyless, shared by everyone on your network" in low.allowance_text()
    assert "Running low" in low.allowance_text()


def test_settings_and_doctor_never_show_the_key(settings, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENALEX_API_KEY", SECRET)
    monkeypatch.setenv("PAPERSCOUT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PAPERSCOUT_PROVIDER", "arxiv")  # the paper-provider probe isn't what this test checks
    monkeypatch.delenv("S2_API_KEY", raising=False)
    loaded = load_settings(tmp_path)
    assert loaded.openalex_api_key == SECRET and loaded.redacted()["openalex_api_key"] == "set"
    report = doctor.run_doctor(loaded, live=True, transport=httpx.MockTransport(Recorder()))
    text = doctor.render(report)
    assert "OPENALEX_API_KEY: configured; value not shown" in text
    assert "Live probe to OpenAlex" in text and "$0.850 of $1.00 left" in text
    assert SECRET not in text and SECRET not in str(report)
