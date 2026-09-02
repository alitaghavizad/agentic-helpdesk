"""The admin API's published schema and the trace size cap.

Phase 8b builds its screens against this API's OpenAPI document, so an
endpoint that publishes an empty schema is not a cosmetic problem: it is
the difference between a generated client that knows `cost_usd` from
`cache_hit_rate` and one that gets `object`.

These are the two review findings deferred from task 2 until the whole
read surface existed, so it could be typed once.
"""
from __future__ import annotations

import uuid

import pytest

from app.admin import router as admin_router
from app.admin.schemas import PageResponse, RunSummary
from app.db.models import Role, User


def _login(client, db_session, *, username: str, role: Role):
    from app.auth.security import hash_password

    user = User(
        username=username, email=f"{username}@northstar.example", full_name=username.title(),
        password_hash=hash_password("Passw0rd!dev"), role=role,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _admin(client, db_session):
    return _login(client, db_session, username=f"sc{uuid.uuid4().hex[:12]}", role=Role.ADMIN)


# ------------------------------------------------- the published schema

# Every admin read endpoint, with the component its response must resolve
# to. Before this round, five of these published `items: list[object]` and
# three published nothing at all.
TYPED_ENDPOINTS = [
    ("/api/admin/overview", "Overview"),
    ("/api/admin/costs", "Costs"),
    ("/api/admin/runs", "PageResponse_RunSummary_"),
    ("/api/admin/runs/{run_id}/trace", "RunTrace"),
    ("/api/admin/conversations", "PageResponse_ConversationSummary_"),
    ("/api/admin/audit", "PageResponse_AuditEntry_"),
    ("/api/admin/users", "PageResponse_UserSummary_"),
    ("/api/admin/lessons", "PageResponse_LessonSummary_"),
]


@pytest.mark.parametrize("path,component", TYPED_ENDPOINTS)
def test_every_admin_read_endpoint_publishes_a_named_schema(client, path, component):
    schema = client.get("/openapi.json").json()
    response = schema["paths"][path]["get"]["responses"]["200"]
    ref = response["content"]["application/json"]["schema"].get("$ref", "")
    assert ref.endswith(f"/{component}"), f"{path} published {ref!r}"


def test_a_paged_endpoints_item_schema_is_not_an_open_object(client):
    """The specific regression: `items: list[dict]` publishes
    `{"type": "array", "items": {"type": "object"}}`, which type-checks
    every possible payload and describes none of them."""
    schema = client.get("/openapi.json").json()
    page = schema["components"]["schemas"]["PageResponse_RunSummary_"]
    items = page["properties"]["items"]
    assert items["type"] == "array"
    assert "$ref" in items["items"], f"items are still untyped: {items['items']}"

    run = schema["components"]["schemas"]["RunSummary"]
    assert set(run["properties"]) == {
        "id", "trigger", "status", "started_at", "duration_ms", "cost_usd",
        "llm_calls", "tool_calls", "error",
    }


def test_the_envelope_is_generic_not_copied_per_endpoint(client):
    """One envelope, parameterised -- not five hand-written near-copies
    that can drift apart in what `total` means."""
    schema = client.get("/openapi.json").json()
    envelopes = [
        name for name in schema["components"]["schemas"] if name.startswith("PageResponse")
    ]
    assert len(envelopes) == 5
    for name in envelopes:
        assert set(schema["components"]["schemas"][name]["properties"]) == {
            "items", "total", "limit", "offset",
        }


def test_a_payload_that_does_not_match_its_item_model_is_caught(client, db_session):
    """The declaration is only worth having if it is enforced. FastAPI
    validates the handler's dict against the response model, so a router
    that dropped a field would fail here rather than quietly shipping a
    payload the published schema says is impossible."""
    with pytest.raises(Exception):
        PageResponse[RunSummary](items=[{"id": "x"}], total=0, limit=50, offset=0)


# ------------------------------------------------- the trace cap


def _tree(depth: int = 0, breadth: int = 0, count: int = 0):
    """A stand-in for tracing.RunTrace's node shape: anything with `.span`
    and `.children`. Built directly rather than through the database
    because the cap is arithmetic over the tree, and inserting 600 real
    spans to test it would make a slow test that proves the same thing."""
    class _Span:
        def __init__(self, i):
            self.id = uuid.uuid4()
            self.kind = type("K", (), {"value": "llm"})()
            self.status = type("S", (), {"value": "ok"})()
            self.name = f"span-{i}"
            self.duration_ms = 1
            self.model = None
            self.input_tokens = self.output_tokens = 0
            self.cache_read_tokens = self.cache_write_tokens = 0
            self.cost_usd = None
            self.input = self.output = None
            self.error = None

    class _Node:
        def __init__(self, i):
            self.span = _Span(i)
            self.children = []

    return _Node


def _flat_roots(n: int):
    node_cls = _tree()
    return [node_cls(i) for i in range(n)]


def _deep_chain(n: int):
    node_cls = _tree()
    root = node_cls(0)
    cursor = root
    for i in range(1, n):
        child = node_cls(i)
        cursor.children.append(child)
        cursor = child
    return [root]


def test_a_trace_under_the_cap_is_not_truncated():
    roots, count, truncated = admin_router._span_forest(_flat_roots(10), cap=500)
    assert count == 10 and truncated is False
    assert len(roots) == 10


@pytest.mark.parametrize("build", [_flat_roots, _deep_chain])
def test_a_trace_over_the_cap_is_capped_and_says_so(build):
    """Both shapes matter: a wide trace (many roots) and a deep one (one
    long tool chain) reach the cap by different paths, and a cap that only
    counted roots would miss the second entirely."""
    _roots, count, truncated = admin_router._span_forest(build(60), cap=25)
    assert count == 25, "the cap must bound spans, not roots"
    assert truncated is True


def test_the_cap_stops_the_work_not_just_the_output(monkeypatch):
    """A cap that trims the response after building the whole tree is not a
    cap -- it is the same memory and the same CPU, discarded at the end.

    This is a real defect this test was written for: _span_node used to
    recurse into its own children, and _span_forest then overwrote the
    `children` key it had just built. Every assertion about output shape
    passed, because the output WAS correct. Only counting the work catches
    it.
    """
    calls = []
    real = admin_router._span_node
    monkeypatch.setattr(
        admin_router, "_span_node", lambda node: (calls.append(node), real(node))[1],
    )

    _roots, count, truncated = admin_router._span_forest(_deep_chain(600), cap=25)
    assert count == 25 and truncated is True
    assert len(calls) == 25, (
        f"serialised {len(calls)} spans to return 25 -- the cap trimmed the "
        "output but not the work"
    )


def test_a_truncated_trace_is_a_correct_prefix_not_a_shuffled_sample():
    """Depth-first, so each parent stays adjacent to the children it
    spawned. A breadth-first cut would return all 60 roots with none of
    their bodies, which reads as 60 runs that did nothing."""
    roots, count, _truncated = admin_router._span_forest(_deep_chain(60), cap=5)
    assert count == 5
    names = []
    cursor = roots[0]
    while cursor:
        names.append(cursor["name"])
        cursor = cursor["children"][0] if cursor["children"] else None
    assert names == [f"span-{i}" for i in range(5)]


def test_the_endpoint_reports_the_span_count_and_flag(client, db_session, cleanup_run):
    from app.tracing.store import insert_run

    _user, headers = _admin(client, db_session)
    run_id = insert_run(trigger=__import__(
        "app.db.models", fromlist=["RunTrigger"],
    ).RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    try:
        body = client.get(f"/api/admin/runs/{run_id}/trace", headers=headers).json()
        assert body["span_count"] == 0
        assert body["truncated"] is False
    finally:
        cleanup_run(run_id)
