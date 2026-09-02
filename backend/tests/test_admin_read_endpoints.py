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
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text as sa_text

from app.admin import queries
from app.db.models import (
    ActorType, Conversation, Role, RunTrigger, SpanKind, SpanStatus, User,
)


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


def _insert_span(*, run_id, span_id, parent_span_id, sequence, name):
    """Thin wrapper over tracing.store.insert_span, which takes every column
    as a required keyword. Only run/parent/sequence/name vary here."""
    from app.tracing.store import insert_span

    now = datetime.now(timezone.utc)
    insert_span(
        run_id=run_id, span_id=span_id, parent_span_id=parent_span_id, sequence=sequence,
        kind=SpanKind.LLM, name=name, status=SpanStatus.OK,
        started_at=now, ended_at=now + timedelta(milliseconds=5), duration_ms=5,
        input={"prompt": "p"}, output={"text": "t"}, model="test-model",
        input_tokens=1, output_tokens=1, cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=Decimal("0.000001"), error=None, metadata={},
    )


def test_run_trace_returns_a_nested_span_tree(client, db_session, cleanup_run):
    """Builds the parent/child pair instead of picking whatever run happens to
    be first in the table.

    That distinction is the whole test. Of the 20,900 spans in development
    only 2 have a parent at all, so a test that traces an arbitrary run has
    only ever seen a FLAT list -- and the previous version of this test,
    which asserted no more than `isinstance(node["children"], list)`, passed
    unchanged when the recursion in router._span_node was replaced with a
    hardcoded `"children": []`. A test named "nested span tree" that cannot
    tell a tree from a list is not testing anything.

    tracing.store commits on its own connection by design, so these rows do
    NOT roll back with db_session -- hence cleanup_run in the finally."""
    from app.tracing.store import insert_run

    _user, headers = _admin(client, db_session)
    run_id = insert_run(trigger=RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    try:
        parent_id, child_id = uuid.uuid4(), uuid.uuid4()
        _insert_span(
            run_id=run_id, span_id=parent_id, parent_span_id=None, sequence=0, name="parent",
        )
        _insert_span(
            run_id=run_id, span_id=child_id, parent_span_id=parent_id, sequence=1, name="child",
        )

        response = client.get(f"/api/admin/runs/{run_id}/trace", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"run", "roots", "span_count", "truncated"}
        assert body["span_count"] == 2 and body["truncated"] is False
        assert body["run"]["id"] == str(run_id)

        # Exactly one root: the child must be nested UNDER the parent, not
        # returned alongside it. A flattening bug shows up here as len 2.
        assert len(body["roots"]) == 1, body["roots"]
        root = body["roots"][0]
        for key in ("id", "kind", "name", "status", "duration_ms", "children"):
            assert key in root, f"span node missing {key}"
        assert root["id"] == str(parent_id)
        assert len(root["children"]) == 1, root["children"]
        assert root["children"][0]["id"] == str(child_id)
        assert root["children"][0]["name"] == "child"
        # The recursion has to produce full nodes, not id stubs.
        assert root["children"][0]["children"] == []
        assert root["children"][0]["model"] == "test-model"
    finally:
        cleanup_run(run_id)


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
    """An empty result is a 200 with zero items and a well-formed envelope,
    never a 404 and never a bare array.

    Emptiness is created by filtering on an action nobody will ever write,
    NOT by assuming the table is empty. It used to assert `total == 0`
    against the whole table -- true only while audit_log happened to be
    empty, and spec 5.4 gives that table no delete path, so the first real
    admin action against a database would have broken this permanently.
    (It did: a reviewer's 64 rows turned it into the suite's only failure.)
    """
    _user, headers = _admin(client, db_session)
    response = client.get(
        "/api/admin/audit", params={"action": f"never.written.{uuid.uuid4().hex}"},
        headers=headers,
    )
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


# ---- Ordering (audit_log is the one table whose purpose is being read in order) ----

def test_audit_rows_written_in_one_transaction_come_back_newest_first(client, db_session):
    """The ordering guarantee, proven against the case that actually breaks it.

    AuditLog.created_at carries `server_default=func.now()`, and in Postgres
    now() is TRANSACTION-start time -- so every audit row written by a single
    mutation would get a byte-identical timestamp and list_audit's ordering
    would fall through to its only tiebreaker, `id.desc()`, which is a random
    uuid4. record_audit therefore stamps created_at per call in Python.

    Eight rows are written here in ONE transaction, which is exactly the
    shape that produced the observed scramble (5,6,3,4,1,7,2,0). Both
    assertions below fail if the explicit stamp is reverted: the ids come
    back shuffled, and the timestamps stop being strictly decreasing."""
    from app.audit.service import record_audit

    _user, headers = _admin(client, db_session)
    action = f"order.probe.{uuid.uuid4().hex[:8]}"
    rows = [
        record_audit(
            db_session, actor_type=ActorType.SYSTEM, actor_id="tester", action=action,
            target_type="widget", target_id=str(n), payload={"n": n},
        )
        for n in range(8)
    ]

    body = client.get(f"/api/admin/audit?action={action}&limit=50", headers=headers).json()
    assert body["total"] == 8
    assert [i["target_id"] for i in body["items"]] == [str(n) for n in reversed(range(8))]
    assert [i["id"] for i in body["items"]] == [str(r.id) for r in reversed(rows)]

    # Strictly decreasing, not merely non-increasing: under the server_default
    # every one of these is equal, so this assertion alone catches the revert
    # even in the 1-in-40320 run where the random uuid4 order happens to be
    # right.
    stamps = [datetime.fromisoformat(i["created_at"]) for i in body["items"]]
    assert all(a > b for a, b in zip(stamps, stamps[1:])), stamps


# ---- Audit date-range filter (spec 4: "filterable by ... date range") -------------

def _audit_pair(db_session, action):
    """Two audit rows with distinct, known created_at values, oldest first."""
    from app.audit.service import record_audit

    older = record_audit(
        db_session, actor_type=ActorType.SYSTEM, actor_id="tester", action=action,
        target_type="widget", target_id="older", payload={},
    )
    newer = record_audit(
        db_session, actor_type=ActorType.SYSTEM, actor_id="tester", action=action,
        target_type="widget", target_id="newer", payload={},
    )
    assert newer.created_at > older.created_at
    return older, newer


def test_audit_since_includes_a_row_exactly_on_the_boundary(client, db_session):
    """[since, ...) is closed at the bottom: `since` == a row's own timestamp
    must return that row. An off-by-one here silently drops the first event of
    every window an investigator asks for."""
    _user, headers = _admin(client, db_session)
    action = f"range.since.{uuid.uuid4().hex[:8]}"
    _older, newer = _audit_pair(db_session, action)

    body = client.get(
        "/api/admin/audit",
        params={"action": action, "since": newer.created_at.isoformat()},
        headers=headers,
    ).json()
    assert [i["target_id"] for i in body["items"]] == ["newer"]
    assert body["total"] == 1


def test_audit_until_excludes_a_row_exactly_on_the_boundary(client, db_session):
    """[..., until) is open at the top, so two adjacent windows tile the
    timeline without reporting the boundary row twice."""
    _user, headers = _admin(client, db_session)
    action = f"range.until.{uuid.uuid4().hex[:8]}"
    _older, newer = _audit_pair(db_session, action)

    body = client.get(
        "/api/admin/audit",
        params={"action": action, "until": newer.created_at.isoformat()},
        headers=headers,
    ).json()
    assert [i["target_id"] for i in body["items"]] == ["older"]
    assert body["total"] == 1


def test_audit_since_and_until_together_select_a_half_open_window(client, db_session):
    _user, headers = _admin(client, db_session)
    action = f"range.both.{uuid.uuid4().hex[:8]}"
    older, newer = _audit_pair(db_session, action)

    both = client.get(
        "/api/admin/audit",
        params={
            "action": action,
            "since": older.created_at.isoformat(),
            "until": newer.created_at.isoformat(),
        },
        headers=headers,
    ).json()
    assert [i["target_id"] for i in both["items"]] == ["older"]

    # The adjacent window starting where the last one ended picks up exactly
    # the row the first one excluded -- no gap, no double count.
    adjacent = client.get(
        "/api/admin/audit",
        params={"action": action, "since": newer.created_at.isoformat()},
        headers=headers,
    ).json()
    assert [i["target_id"] for i in adjacent["items"]] == ["newer"]

    empty = client.get(
        "/api/admin/audit",
        params={
            "action": action,
            "since": (newer.created_at + timedelta(seconds=1)).isoformat(),
        },
        headers=headers,
    ).json()
    assert empty["items"] == [] and empty["total"] == 0


def test_audit_a_naive_date_bound_is_read_as_utc(client, db_session):
    """`?since=2026-08-29T00:00:00` with no offset parses into a NAIVE
    datetime. The column is timestamptz, so a naive bind would be interpreted
    in the database session's TimeZone; queries._as_utc defines it as UTC
    instead. Asserted by giving the naive and the aware form of the same
    instant and requiring the same answer.

    THE SESSION TIMEZONE IS FORCED OFF UTC FIRST, and that is what makes
    this test mean anything. The development database runs `Etc/UTC`, so a
    naive bind is already interpreted as UTC and both forms agree whether
    or not _as_utc exists -- measured: deleting the _as_utc calls left this
    test green. Under America/New_York the two forms differ by five hours,
    so only the conversion can make them agree."""
    _user, headers = _admin(client, db_session)
    db_session.execute(sa_text("SET LOCAL TIME ZONE 'America/New_York'"))
    action = f"range.naive.{uuid.uuid4().hex[:8]}"
    _older, newer = _audit_pair(db_session, action)

    aware = newer.created_at.astimezone(timezone.utc)
    naive = aware.replace(tzinfo=None)
    assert "+" not in naive.isoformat(), "the naive form must carry no offset"

    from_naive = client.get(
        "/api/admin/audit", params={"action": action, "since": naive.isoformat()},
        headers=headers,
    ).json()
    from_aware = client.get(
        "/api/admin/audit", params={"action": action, "since": aware.isoformat()},
        headers=headers,
    ).json()
    assert [i["target_id"] for i in from_naive["items"]] == ["newer"]
    assert from_naive["items"] == from_aware["items"]
    assert from_naive["total"] == from_aware["total"] == 1


def test_audit_an_unparseable_date_bound_is_rejected(client, db_session):
    _user, headers = _admin(client, db_session)
    assert client.get("/api/admin/audit?since=yesterday", headers=headers).status_code == 422


# ---- Conversation participant search (spec 4: "by title and participant") ---------

def _authenticated_conversation(db_session, marker):
    """A conversation owned by a real user. The `conversations` CHECK
    constraint is XOR -- setting user_id forbids guest_name/guest_email -- so
    this row's guest columns are NULL by force of the schema, which is
    precisely why searching guest_name could never find it."""
    from app.auth.security import hash_password

    owner = User(
        username=f"ada{marker}", email=f"ada{marker}@northstar.example",
        full_name=f"Ada Lovelace {marker}",  # NOT NULL, no default
        password_hash=hash_password("Passw0rd!dev"), role=Role.EMPLOYEE,
    )
    db_session.add(owner)
    db_session.flush()
    conv = Conversation(user_id=owner.id, title="Untitled")
    db_session.add(conv)
    db_session.flush()
    return owner, conv


def test_conversation_search_finds_an_authenticated_participant_by_username(client, db_session):
    """The bug this replaces: the filter was `title ILIKE q OR guest_name
    ILIKE q`, and the XOR CHECK constraint guarantees guest_name IS NULL
    whenever user_id is set. Participant search was therefore 0%-effective
    for every logged-in user's conversation -- it could only ever find
    guests."""
    _user, headers = _admin(client, db_session)
    marker = uuid.uuid4().hex[:12]
    owner, conv = _authenticated_conversation(db_session, marker)
    decoy = Conversation(
        guest_name="Unrelated Guest", guest_email="unrelated@northstar.example",
        title="Printer jam",
    )
    db_session.add(decoy)
    db_session.flush()

    body = client.get(f"/api/admin/conversations?q=ada{marker}", headers=headers).json()
    assert [i["id"] for i in body["items"]] == [str(conv.id)]
    # total and items come from the same joined query object; if the count
    # query had lost the join it would count different rows and the pager
    # would disagree with the page it is paging.
    assert body["total"] == len(body["items"]) == 1
    assert body["items"][0]["user_id"] == str(owner.id)
    assert body["items"][0]["guest_name"] is None


def test_conversation_search_finds_an_authenticated_participant_by_full_name(client, db_session):
    _user, headers = _admin(client, db_session)
    marker = uuid.uuid4().hex[:12]
    _owner, conv = _authenticated_conversation(db_session, marker)

    body = client.get(
        "/api/admin/conversations", params={"q": f"Lovelace {marker}"}, headers=headers,
    ).json()
    assert [i["id"] for i in body["items"]] == [str(conv.id)]
    assert body["total"] == len(body["items"]) == 1


def test_conversation_search_finds_a_guest_by_email(client, db_session):
    _user, headers = _admin(client, db_session)
    marker = uuid.uuid4().hex[:12]
    match = Conversation(
        guest_name="Grace", guest_email=f"grace{marker}@northstar.example", title="Untitled",
    )
    decoy = Conversation(
        guest_name="Grace", guest_email="grace@elsewhere.example", title="Untitled",
    )
    db_session.add_all([match, decoy])
    db_session.flush()

    body = client.get(f"/api/admin/conversations?q=grace{marker}", headers=headers).json()
    assert [i["id"] for i in body["items"]] == [str(match.id)]
    assert body["total"] == len(body["items"]) == 1


def test_conversation_search_matching_nothing_returns_an_empty_page(client, db_session):
    _user, headers = _admin(client, db_session)
    marker = uuid.uuid4().hex[:12]
    _owner, _conv = _authenticated_conversation(db_session, marker)
    db_session.add(Conversation(
        guest_name="Guest", guest_email="g@northstar.example", title="Printer jam",
    ))
    db_session.flush()

    body = client.get(f"/api/admin/conversations?q=zz-no-such-{marker}", headers=headers).json()
    assert body["items"] == []
    assert body["total"] == 0


def test_conversation_list_still_includes_guests_after_the_user_join(client, db_session):
    """The participant join has to be an OUTER join. An inner join would make
    this list -- and every search over it -- silently drop every guest
    conversation, which is most of them."""
    _user, headers = _admin(client, db_session)
    marker = uuid.uuid4().hex[:12]
    _owner, owned = _authenticated_conversation(db_session, marker)
    guest = Conversation(
        guest_name=f"Guest {marker}", guest_email=f"g{marker}@northstar.example",
        title=f"Ticket {marker}",
    )
    db_session.add(guest)
    db_session.flush()

    body = client.get(f"/api/admin/conversations?q={marker}", headers=headers).json()
    assert {i["id"] for i in body["items"]} == {str(owned.id), str(guest.id)}
    assert body["total"] == len(body["items"]) == 2


# ---- ILIKE metacharacters and empty filters --------------------------------------

def test_a_bare_percent_query_is_a_literal_not_a_wildcard(client, db_session):
    """`%` is an ILIKE wildcard, so an unescaped search box answers `q=%` with
    the entire table -- a full dump from a field the user believes is a
    substring search."""
    _user, headers = _admin(client, db_session)
    marker = uuid.uuid4().hex[:12]
    conv = Conversation(
        guest_name=f"Guest {marker}", guest_email=f"g{marker}@northstar.example",
        title=f"VPN outage {marker}",
    )
    db_session.add(conv)
    db_session.flush()

    unfiltered = client.get("/api/admin/conversations", headers=headers).json()
    assert unfiltered["total"] > 0
    assert str(conv.id) in {i["id"] for i in unfiltered["items"]}

    body = client.get("/api/admin/conversations", params={"q": "%"}, headers=headers).json()
    assert str(conv.id) not in {i["id"] for i in body["items"]}
    assert body["items"] == []
    assert body["total"] == 0


def test_an_underscore_in_a_query_is_a_literal_not_a_single_char_wildcard(client, db_session):
    """`P_inter` matches "Printer" while `_` is left as a wildcard."""
    _user, headers = _admin(client, db_session)
    marker = uuid.uuid4().hex[:12]
    conv = Conversation(
        guest_name="Guest", guest_email="g@northstar.example", title=f"Printer jam {marker}",
    )
    db_session.add(conv)
    db_session.flush()

    hit = client.get("/api/admin/conversations", params={"q": "Printer"}, headers=headers).json()
    assert str(conv.id) in {i["id"] for i in hit["items"]}

    body = client.get("/api/admin/conversations", params={"q": "P_inter"}, headers=headers).json()
    assert str(conv.id) not in {i["id"] for i in body["items"]}
    assert body["total"] == 0


def test_a_backslash_in_a_query_is_a_literal(client, db_session):
    """The escape character itself has to survive escaping, or a query ending
    in a backslash becomes a dangling escape and Postgres raises "LIKE pattern
    must not end with escape character" -- a 500 out of a search box."""
    _user, headers = _admin(client, db_session)
    marker = uuid.uuid4().hex[:12]
    conv = Conversation(
        guest_name="Guest", guest_email="g@northstar.example",
        title=f"C:\\Users\\{marker}",
    )
    db_session.add(conv)
    db_session.flush()

    trailing = client.get("/api/admin/conversations", params={"q": "\\"}, headers=headers)
    assert trailing.status_code == 200, trailing.text
    assert str(conv.id) in {i["id"] for i in trailing.json()["items"]}

    body = client.get(
        "/api/admin/conversations", params={"q": f"Users\\{marker}"}, headers=headers,
    ).json()
    assert [i["id"] for i in body["items"]] == [str(conv.id)]


def test_an_empty_q_lists_everything_deliberately(client, db_session):
    """`?q=` (a cleared search box) lists the table rather than returning
    nothing. That is the intended behaviour, not an accident of `if q:` --
    queries.list_conversations spells it `if q and q.strip()`. Pinned here so
    a later "tightening" of that check has to argue with a test."""
    _user, headers = _admin(client, db_session)
    db_session.add(Conversation(
        guest_name="Guest", guest_email="g@northstar.example", title="Anything",
    ))
    db_session.flush()

    unfiltered = client.get("/api/admin/conversations", headers=headers).json()
    for blank in ("", "   "):
        body = client.get("/api/admin/conversations", params={"q": blank}, headers=headers).json()
        assert body["total"] == unfiltered["total"] > 0


def test_an_empty_audit_filter_lists_everything_deliberately(client, db_session):
    """The same decision on the audit list's exact-match filters: a blank
    action/actor/target means "no filter", not "match the empty string" --
    which would match nothing at all, since no audit row has a blank one."""
    from app.audit.service import record_audit

    _user, headers = _admin(client, db_session)
    row = record_audit(
        db_session, actor_type=ActorType.SYSTEM, actor_id="tester",
        action=f"blank.probe.{uuid.uuid4().hex[:8]}", target_type="widget",
        target_id="1", payload={},
    )

    unfiltered = client.get("/api/admin/audit", headers=headers).json()
    assert str(row.id) in {i["id"] for i in unfiltered["items"]}
    for params in ({"action": ""}, {"actor_id": "  "}, {"target_type": ""}):
        body = client.get("/api/admin/audit", params=params, headers=headers).json()
        assert body["total"] == unfiltered["total"] > 0
