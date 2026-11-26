"""Tests for the NetBox object cache in ObjectCachingSession."""

from unittest.mock import MagicMock, patch

import pytest
from requests import Session
from requests.adapters import HTTPAdapter

from netbox_zabbix_sync.modules.api_clients import (
    ObjectCachingSession,
    RetryingNetBoxAPI,
)

SITE = "http://netbox.example/api/dcim/sites/12/"


def response(ok=True):
    resp = MagicMock(name="Response")
    resp.ok = ok
    return resp


@pytest.fixture
def sent():
    """Stand in for the network: every request that leaves the session."""
    with patch.object(Session, "request", side_effect=lambda *a, **k: response()) as m:
        yield m


@pytest.fixture
def session():
    s = ObjectCachingSession()
    s.enable_cache()
    return s


def test_object_is_fetched_once(session, sent):
    first = session.get(SITE)
    second = session.get(SITE)
    assert sent.call_count == 1
    assert first is second


def test_nothing_is_cached_while_disabled(sent):
    session = ObjectCachingSession()
    session.get(SITE)
    session.get(SITE)
    assert sent.call_count == 2  # noqa: PLR2004


def test_disabling_drops_the_cache(session, sent):
    session.get(SITE)
    session.disable_cache()
    session.enable_cache()
    session.get(SITE)
    assert sent.call_count == 2  # noqa: PLR2004


def test_lists_are_not_cached(session, sent):
    session.get("http://netbox.example/api/dcim/sites/")
    session.get("http://netbox.example/api/dcim/sites/")
    session.get(SITE, params={"brief": 1})
    session.get(SITE, params={"brief": 1})
    assert sent.call_count == 4  # noqa: PLR2004


def test_failed_responses_are_not_cached(session):
    with patch.object(Session, "request", side_effect=[response(ok=False), response()]):
        assert not session.get(SITE).ok
        assert session.get(SITE).ok


def test_same_object_through_another_host_is_a_hit(session, sent):
    """NetBox builds object URLs from the host it sees, which may differ behind a proxy."""
    session.get(SITE)
    session.get("https://proxy.example/api/dcim/sites/12/")
    assert sent.call_count == 1


@pytest.mark.parametrize(
    "write",
    [
        ("patch", "http://netbox.example/api/dcim/sites/12/"),
        ("patch", "http://netbox.example/api/dcim/sites/"),  # bulk update
        ("delete", "http://netbox.example/api/dcim/sites/12/"),
    ],
)
def test_writing_to_the_endpoint_drops_its_objects(session, sent, write):
    method, url = write
    session.get(SITE)
    getattr(session, method)(url)
    session.get(SITE)
    gets = [c for c in sent.call_args_list if c.args[0] == "GET"]
    assert len(gets) == 2  # noqa: PLR2004


def test_writing_elsewhere_keeps_the_cache(session, sent):
    session.get(SITE)
    session.post("http://netbox.example/api/extras/journal-entries/")
    session.get(SITE)
    gets = [c for c in sent.call_args_list if c.args[0] == "GET"]
    assert len(gets) == 1


def test_netbox_api_uses_the_caching_session():
    api = RetryingNetBoxAPI("http://netbox.example", token="x")
    assert isinstance(api.http_session, ObjectCachingSession)
    # The retry adapter is mounted on the session that is actually used.
    adapter = api.http_session.get_adapter("http://netbox.example")
    assert isinstance(adapter, HTTPAdapter)
    assert adapter.max_retries.total
