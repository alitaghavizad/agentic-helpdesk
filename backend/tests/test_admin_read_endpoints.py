"""The six admin read endpoints (spec 14/15): overview, runs, trace,
conversations, audit and costs.

Two things these tests are deliberately strict about.

First, authorization is proven per-endpoint rather than per-router. A router
prefix is not a permission; a single route that forgets `AdminPrincipal`
exposes the whole run history, so every path is asserted against every
non-admin principal -- employee, helpdesk AND guest.

Second, every filter test proves BOTH directions. `audit_log` and
`conversations` are empty tables at this phase, so a filter asserted only
against a string that matches nothing passes identically whether the filter
works or the WHERE clause was never emitted. Each filter test therefore
inserts a row it expects to match and asserts the matching query finds it
and a near-miss query does not.
"""
from __future__ import annotations

import uuid

import pytest

from app.admin import queries
from app.db.models import ActorType, Conversation, Role, Span, User


def _login(client, db_session, *, username: str, role: Role, helpdesk_ref: str | None = None):
    """Copied from tests/test_tickets_router.py -- there is no shared auth
    fixture in this project. `full_name` is NOT NULL with no default, so it
    must be set explicitly on every User built in a test."""
    from app.auth.security import hash_password

    user = User(
        username=username, email=f"{username}@northstar.example", full_name=username.title(),
        password_hash=hash_password("Passw0rd!dev"), role=role, helpdesk_ref=helpdesk_ref,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _guest_login(client) -> dict:
    """Copied from tests/test_notifications_router.py. A guest principal has
    kind='guest', role='guest' and user_id=None."""
    resp = client.post("/api/auth/guest", json={"name": "Visitor", "email": "visitor@example.com"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _admin(client, db_session):
    return _login(client, db_session, username=f"rd{uuid.uuid4().hex[:12]}", role=Role.ADMIN)


# Every read path, including the parameterised trace route: authorization is
# checked before the run id is ever looked up, so a random uuid is enough.
READ_PATHS = [
    "/api/admin/overview",
    "/api/admin/runs",
    f"/api/admin/runs/{uuid.uuid4()}/trace",
    "/api/admin/conversations",
    "/api/admin/audit",
    "/api/admin/costs",
]


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.HELPDESK])
def test_every_read_endpoint_rejects_non_admins(client, db_session, path, role):
    _user, headers = _login(
        client, db_session, username=f"rd{uuid.uuid4().hex[:12]}", role=role,
    )
    assert client.get(path, headers=headers).status_code == 403


@pytest.mark.parametrize("path", READ_PATHS)
def test_every_read_endpoint_rejects_guests(client, path):
    """A guest carries a valid token with role='guest'. Whether the stack
    rejects it as unauthenticated-for-this-purpose (401) or forbidden (403)
    is an implementation detail; that it never returns data is not."""
    headers = _guest_login(client)
    response = client.get(path, headers=headers)
    assert response.status_code in (401, 403), response.text


@pytest.mark.parametrize("path", READ_PATHS)
def test_every_read_endpoint_rejects_anonymous_callers(client, path):
    assert client.get(path).status_code == 401


def test_overview_returns_every_counter(client, db_session):
    _user, headers = _admin(client, db_session)
    response = client.get("/api/admin/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "runs_today", "spend_today", "pending_approvals", "open_tickets", "error_rate",
    }
    assert isinstance(body["runs_today"], int)
    assert isinstance(body["pending_approvals"], int)
    assert isinstance(body["open_tickets"], int)
    assert 0.0 <= body["error_rate"] <= 1.0
    assert body["spend_today"] >= 0.0


def test_runs_list_is_paginated_and_reports_its_total(client, db_session):
    _user, headers = _admin(client, db_session)
    response = client.get("/api/admin/runs?limit=5", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["limit"] == 5
    assert body["offset"] == 0
    assert len(body["items"]) <= 5
    assert body["total"] >= len(body["items"])
    if body["items"]:
        assert set(body["items"][0]) == {
            "id", "trigger", "status", "started_at", "duration_ms", "cost_usd",
            "llm_calls", "tool_calls", "error",
        }


def test_offset_actually_moves_the_window(client, db_session):
    """A limit that is honoured while offset is ignored still passes every
    shape assertion above, and silently returns page one forever."""
    _user, headers = _admin(client, db_session)
    first = client.get("/api/admin/runs?limit=1&offset=0", headers=headers).json()
    second = client.get("/api/admin/runs?limit=1&offset=1", headers=headers).json()
    if first["total"] < 2:
        pytest.skip("fewer than two runs in the database")
    assert len(first["items"]) == 1 and len(second["items"]) == 1
    assert second["offset"] == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_an_over_large_limit_is_clamped_not_rejected(client, db_session):
    """20,000+ spans and 500+ runs exist. An endpoint that honours
    limit=100000 is a production hazard; one that 422s on it is merely
    annoying."""
    _user, headers = _admin(client, db_session)
    response = client.get("/api/admin/runs?limit=100000", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["limit"] == queries.MAX_LIMIT == 200
    assert len(response.json()["items"]) <= queries.MAX_LIMIT


def test_a_nonsense_limit_is_clamped_up_not_rejected(client, db_session):
    _user, headers = _admin(client, db_session)
    response = client.get("/api/admin/runs?limit=0&offset=-4", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["limit"] == 1
    assert response.json()["offset"] == 0


def test_run_trace_returns_a_nested_span_tree(client, db_session):
    _user, headers = _admin(client, db_session)
    run_id = db_session.query(Span.run_id).limit(1).scalar()
    if run_id is None:
        pytest.skip("no spans in the database to trace")

    response = client.get(f"/api/admin/runs/{run_id}/trace", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"run", "roots"}
    assert body["run"]["id"] == str(run_id)
    assert isinstance(body["roots"], list)
    assert body["roots"], "a run with spans must have at least one root span"
    node = body["roots"][0]
    for key in ("id", "kind", "name", "status", "duration_ms", "children"):
        assert key in node, f"span node missing {key}"
    assert isinstance(node["children"], list)


def test_an_unknown_run_trace_is_404(client, db_session):
    """The status code alone proves nothing here: an unregistered route
    returns 404 too, so this test passed before the endpoint existed. The
    body is what distinguishes "the run is not there" from "the endpoint is
    not there", so it is asserted."""
    _user, headers = _admin(client, db_session)
    response = client.get(f"/api/admin/runs/{uuid.uuid4()}/trace", headers=headers)
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "no such run"


def test_audit_over_an_empty_table_is_an_empty_page_not_an_error(client, db_session):
    """audit_log is empty at the start of this phase. An empty list is a 200
    with zero items and a well-formed envelope, never a 404 and never a bare
    array."""
    _user, headers = _admin(client, db_session)
    response = client.get("/api/admin/audit", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert isinstance(body["total"], int)
    assert body["limit"] == queries.DEFAULT_LIMIT
    assert body["offset"] == 0
    assert body["items"] == []
    assert body["total"] == 0


def test_audit_can_be_filtered_by_action(client, db_session):
    """Both directions. Against an empty table a broken filter and a working
    one are indistinguishable, so this inserts a row the filter must find and
    then proves a near-miss action excludes it."""
    from app.audit.service import record_audit

    _user, headers = _admin(client, db_session)
    action = f"test.filter.{uuid.uuid4().hex[:8]}"
    row = record_audit(
        db_session, actor_type=ActorType.SYSTEM, actor_id="tester", action=action,
        target_type="widget", target_id=str(uuid.uuid4()), payload={"k": "v"},
    )

    matched = client.get(f"/api/admin/audit?action={action}", headers=headers)
    assert matched.status_code == 200, matched.text
    assert [item["id"] for item in matched.json()["items"]] == [str(row.id)]
    assert matched.json()["total"] == 1
    item = matched.json()["items"][0]
    assert item["action"] == action
    assert item["actor_type"] == "system"
    assert item["actor_id"] == "tester"
    assert item["target_type"] == "widget"
    assert item["payload"] == {"k": "v"}

    excluded = client.get(f"/api/admin/audit?action={action}-nope", headers=headers)
    assert excluded.status_code == 200, excluded.text
    assert excluded.json()["items"] == []
    assert excluded.json()["total"] == 0


def test_audit_can_be_filtered_by_target_type_and_actor(client, db_session):
    from app.audit.service import record_audit

    _user, headers = _admin(client, db_session)
    actor = f"actor-{uuid.uuid4().hex[:8]}"
    target_type = f"tt-{uuid.uuid4().hex[:8]}"
    row = record_audit(
        db_session, actor_type=ActorType.USER, actor_id=actor, action="thing.done",
        target_type=target_type, target_id="1", payload={},
    )
    record_audit(
        db_session, actor_type=ActorType.USER, actor_id="someone-else", action="thing.done",
        target_type="other", target_id="2", payload={},
    )

    by_actor = client.get(f"/api/admin/audit?actor_id={actor}", headers=headers).json()
    assert [i["id"] for i in by_actor["items"]] == [str(row.id)]

    by_target = client.get(f"/api/admin/audit?target_type={target_type}", headers=headers).json()
    assert [i["id"] for i in by_target["items"]] == [str(row.id)]

    assert client.get("/api/admin/audit?actor_id=nobody", headers=headers).json()["items"] == []


def test_conversations_can_be_searched_by_title(client, db_session):
    _user, headers = _admin(client, db_session)
    marker = uuid.uuid4().hex[:12]
    match = Conversation(
        guest_name="Searchable Guest", guest_email="s@northstar.example",
        title=f"VPN outage {marker}",
    )
    decoy = Conversation(
        guest_name="Other Guest", guest_email="o@northstar.example", title="Printer jam",
    )
    db_session.add_all([match, decoy])
    db_session.flush()

    found = client.get(f"/api/admin/conversations?q={marker}", headers=headers)
    assert found.status_code == 200, found.text
    assert [i["id"] for i in found.json()["items"]] == [str(match.id)]
    assert found.json()["total"] == 1
    item = found.json()["items"][0]
    assert item["title"] == f"VPN outage {marker}"
    assert item["guest_name"] == "Searchable Guest"
    assert item["status"] == "active"

    missing = client.get(f"/api/admin/conversations?q=no-such-{marker}", headers=headers)
    assert missing.status_code == 200, missing.text
    assert missing.json()["items"] == []
    assert missing.json()["total"] == 0


def test_conversations_search_also_matches_the_guest_name(client, db_session):
    """The filter is an OR across title and guest name; without this the
    guest-name branch is never executed by any test."""
    _user, headers = _admin(client, db_session)
    marker = uuid.uuid4().hex[:12]
    match = Conversation(
        guest_name=f"Ada {marker}", guest_email="ada@northstar.example", title="Untitled",
    )
    db_session.add(match)
    db_session.flush()

    found = client.get(f"/api/admin/conversations?q={marker}", headers=headers).json()
    assert [i["id"] for i in found["items"]] == [str(match.id)]


def test_conversations_without_a_query_lists_everything_paginated(client, db_session):
    _user, headers = _admin(client, db_session)
    db_session.add_all([
        Conversation(guest_name=f"G{n}", guest_email=f"g{n}@northstar.example", title=f"T{n}")
        for n in range(3)
    ])
    db_session.flush()

    body = client.get("/api/admin/conversations?limit=2", headers=headers).json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["limit"] == 2
    assert len(body["items"]) == 2
    assert body["total"] >= 3


def test_costs_returns_all_four_groupings(client, db_session):
    _user, headers = _admin(client, db_session)
    response = client.get("/api/admin/costs", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"by_day", "by_model", "by_user", "by_trigger", "totals"}
    for key in ("by_day", "by_model", "by_user", "by_trigger"):
        assert isinstance(body[key], list)
    assert set(body["totals"]) == {
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
        "cost_usd", "cache_hit_rate",
    }
    assert 0.0 <= body["totals"]["cache_hit_rate"] <= 1.0
