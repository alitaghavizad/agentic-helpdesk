"""The admin live-activity stream (spec 14: GET /api/admin/runs/stream).

Reuses the notification broker rather than standing up a second pub/sub:
it is already UUID-keyed, already marshals cross-thread offers back onto
the owning event loop, and already carries the SubscriberDropped contract.
A second implementation would have to rediscover all three.

_LiveASGITransport below is copied from tests/test_notifications_router.py
because httpx 0.28's stock ASGITransport cannot stream a response from an
endpoint that never finishes on its own -- see that class's docstring.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import httpx
import pytest

from app.db.models import Role, RunStatus, RunTrigger, User
from app.db.session import get_engine, get_sessionmaker
from app.notifications import broker


# ---------------------------------------------------------------- helpers

def _login(client, db_session, *, username: str, role: Role):
    """Copied from tests/test_admin_read_endpoints.py. `full_name` is NOT
    NULL with no default, so it must be set explicitly."""
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
    return _login(client, db_session, username=f"rs{uuid.uuid4().hex[:12]}", role=Role.ADMIN)


def _guest_login(client) -> dict:
    resp = client.post("/api/auth/guest", json={"name": "Visitor", "email": "visitor@example.com"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


_hard_committed_user_ids: list[uuid.UUID] = []


@pytest.fixture(scope="module", autouse=True)
def _sweep_hard_committed_rows_after_module():
    """`test_an_open_stream_holds_no_pooled_connection` needs an admin that a
    connection OTHER than the test's own can see, so it commits one for real.
    Committed rows are not rolled back at teardown, and a leaked `users` row
    breaks test_seed.py's exact-count assertion for everyone -- so they are
    swept here. Ids are registered the instant they are committed, before
    anything that could fail, so a test that blows up mid-way still gets its
    rows swept.

    refresh_tokens is swept BEFORE users: logging in commits a token row
    whose user_id is a foreign key to users.id."""
    yield
    from app.db.models import RefreshToken

    Session = get_sessionmaker()
    try:
        with Session() as session:
            if _hard_committed_user_ids:
                session.query(RefreshToken).filter(
                    RefreshToken.user_id.in_(_hard_committed_user_ids),
                ).delete(synchronize_session=False)
                session.query(User).filter(
                    User.id.in_(_hard_committed_user_ids),
                ).delete(synchronize_session=False)
            session.commit()
    finally:
        _hard_committed_user_ids.clear()


def _hard_committed_admin() -> tuple[uuid.UUID, str]:
    """`_login`'s hard-committing twin. The username carries a random suffix
    because these rows really do land in the database: a fixed one would
    collide with itself if a previous run's sweep never ran."""
    from app.auth.security import hash_password

    username = f"rsc{uuid.uuid4().hex[:12]}"
    Session = get_sessionmaker()
    with Session() as session:
        user = User(
            username=username, email=f"{username}@northstar.example",
            full_name=username.title(), password_hash=hash_password("Passw0rd!dev"),
            role=Role.ADMIN,
        )
        session.add(user)
        session.commit()
        _hard_committed_user_ids.append(user.id)
        return user.id, username


async def _next_event(lines) -> dict:
    """Skips keepalive comments (`: keepalive`) and the blank line that
    terminates each SSE frame, and returns the next real `data:` payload."""
    async for line in lines:
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError("stream ended before an event arrived")


class _QueueByteStream(httpx.AsyncByteStream):
    """Backs the httpx.Response returned by _LiveASGITransport: an async
    byte-iterable pulled off a queue that the running ASGI app keeps
    feeding, rather than a fixed, pre-collected buffer."""

    def __init__(self, queue: "asyncio.Queue[bytes | None]") -> None:
        self._queue = queue

    async def __aiter__(self):
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            yield chunk


class _LiveASGITransport(httpx.AsyncBaseTransport):
    """Copied from tests/test_notifications_router.py, which carries the full
    explanation. In short: httpx 0.28's stock ASGITransport awaits the whole
    app call before building a Response, so it hangs forever on a stream that
    only ends when the client disconnects. This one runs the app as a
    background task and returns a Response as soon as `http.response.start`
    arrives, which is what lets a test interleave a read of the stream with a
    `broker.publish(...)`.

    Deliberately does NOT normalize an app exception into a clean end of
    body: a real server does not send a tidy final `more_body: False` when
    the app raises mid-stream, and faking one here would make
    test_a_dropped_subscriber_closes_the_stream_instead_of_hanging pass
    whether or not the router actually closes the stream.
    """

    def __init__(self, app) -> None:
        self.app = app
        self._tasks: set[asyncio.Task] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for k, v in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": ("testclient", 123),
            "root_path": "",
        }

        request_body = request.stream.__aiter__()
        request_complete = False

        async def receive():
            nonlocal request_complete
            if request_complete:
                await asyncio.sleep(3600)
            try:
                chunk = await request_body.__anext__()
            except StopAsyncIteration:
                request_complete = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": chunk, "more_body": True}

        headers_ready = asyncio.Event()
        status_holder: dict = {}
        body_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def send(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                status_holder["headers"] = message.get("headers", [])
                headers_ready.set()
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    await body_queue.put(body)
                if not message.get("more_body", False):
                    await body_queue.put(None)

        async def run_app():
            try:
                await self.app(scope, receive, send)
            except Exception:
                headers_ready.set()
                raise

        task = asyncio.ensure_future(run_app())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await headers_ready.wait()

        return httpx.Response(
            status_holder.get("status", 500),
            headers=status_holder.get("headers", []),
            stream=_QueueByteStream(body_queue),
        )

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


# ------------------------------------------------- the publish side


def test_finalize_run_publishes_the_run_summary_to_the_admin_channel(cleanup_run):
    """The publish belongs on the tracing side because that is where a run's
    terminal state is known, and it carries the summary fields rather than a
    bare id so the panel can render a row without a follow-up fetch."""
    from app.tracing.spans import end_run, start_run

    published: list[tuple[uuid.UUID, dict]] = []
    original = broker.publish
    broker.publish = lambda channel, event: published.append((channel, event))
    try:
        handle = start_run(RunTrigger.CHAT_TURN)
        end_run(handle, status=RunStatus.OK)
    finally:
        broker.publish = original
        cleanup_run(handle.run_id)

    admin_events = [e for c, e in published if c == broker.ADMIN_RUNS_CHANNEL]
    assert len(admin_events) == 1, f"expected exactly one admin publish, got {published}"
    event = admin_events[0]
    assert event["type"] == "run_finished"
    assert event["id"] == str(handle.run_id)
    assert event["trigger"] == RunTrigger.CHAT_TURN.value
    assert event["status"] == RunStatus.OK.value
    assert isinstance(event["duration_ms"], int)
    # No spans were recorded, so nothing was priced: None, not 0.0. A run
    # with no priced spans must not be reported as having cost nothing.
    assert event["cost_usd"] is None


def test_a_failed_run_is_published_with_its_error_status(cleanup_run):
    """The Traces screen exists to surface failures, so the failure path is
    the one that must not be dropped."""
    from app.tracing.spans import end_run, start_run

    published: list[dict] = []
    original = broker.publish
    broker.publish = lambda channel, event: published.append(event)
    try:
        handle = start_run(RunTrigger.CHAT_TURN)
        end_run(handle, status=RunStatus.ERROR, error="boom")
    finally:
        broker.publish = original
        cleanup_run(handle.run_id)

    assert [e["status"] for e in published] == [RunStatus.ERROR.value]


def test_the_publish_happens_after_the_commit_not_before(cleanup_run):
    """A subscriber must never be told about a run whose finalisation then
    rolled back. Proven from a THIRD connection: at the moment publish is
    called, an independent session must already be able to see the terminal
    status. If the publish were moved above session.commit(), that
    connection would still read RUNNING and this fails."""
    from app.db.models import Run
    from app.tracing.spans import end_run, start_run

    seen_from_another_connection: list[str] = []

    def _spy(channel, event):
        if channel != broker.ADMIN_RUNS_CHANNEL:
            return
        Session = get_sessionmaker()
        with Session() as probe:
            row = probe.get(Run, uuid.UUID(event["id"]))
            seen_from_another_connection.append(row.status.value)

    original = broker.publish
    broker.publish = _spy
    try:
        handle = start_run(RunTrigger.CHAT_TURN)
        end_run(handle, status=RunStatus.OK)
    finally:
        broker.publish = original
        cleanup_run(handle.run_id)

    assert seen_from_another_connection == [RunStatus.OK.value]


# ------------------------------------------------- authorization


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.HELPDESK])
def test_non_admins_cannot_open_the_stream(client, db_session, role):
    _user, headers = _login(
        client, db_session, username=f"st{uuid.uuid4().hex[:12]}", role=role,
    )
    assert client.get("/api/admin/runs/stream", headers=headers).status_code == 403


def test_guests_cannot_open_the_stream(client):
    headers = _guest_login(client)
    assert client.get("/api/admin/runs/stream", headers=headers).status_code == 403


def test_an_anonymous_request_cannot_open_the_stream(client):
    assert client.get("/api/admin/runs/stream").status_code == 401


# ------------------------------------------------- the stream itself


@pytest.mark.asyncio
async def test_a_published_event_reaches_a_live_admin_subscriber(client, db_session):
    """Drives the ASGI app through _LiveASGITransport rather than the stock
    one, which cannot stream an endpoint that never finishes on its own.
    The `client` fixture is reused only to log in and to get its get_db
    override installed on the shared `app` object."""
    from app.main import app

    _user, headers = _admin(client, db_session)

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/admin/runs/stream", headers=headers) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.aiter_lines()

            broker.publish(broker.ADMIN_RUNS_CHANNEL, {"type": "run_finished", "id": "abc"})
            event = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert event == {"type": "run_finished", "id": "abc"}


@pytest.mark.asyncio
async def test_every_open_admin_stream_sees_the_same_run(client, db_session):
    """Two admins watching the panel at once must both see the activity --
    a fan-out channel, not a hand-off queue. This is the property that
    would break if the endpoint subscribed under the admin's own user id
    instead of the shared sentinel channel."""
    from app.main import app

    _user, headers = _admin(client, db_session)

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/admin/runs/stream", headers=headers) as first:
            async with ac.stream("GET", "/api/admin/runs/stream", headers=headers) as second:
                assert first.status_code == 200 and second.status_code == 200
                first_lines, second_lines = first.aiter_lines(), second.aiter_lines()

                broker.publish(broker.ADMIN_RUNS_CHANNEL, {"type": "run_finished", "id": "shared"})

                assert (await asyncio.wait_for(_next_event(first_lines), timeout=5))["id"] == "shared"
                assert (await asyncio.wait_for(_next_event(second_lines), timeout=5))["id"] == "shared"


@pytest.mark.asyncio
async def test_a_finalised_run_reaches_a_live_admin_subscriber(client, db_session, cleanup_run):
    """The end-to-end wiring: a real run finalisation, through the real
    tracing store, arriving on a real open stream. The tests above pin the
    two halves separately; this one pins that they are connected."""
    from app.main import app
    from app.tracing.spans import end_run, start_run

    _user, headers = _admin(client, db_session)

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/admin/runs/stream", headers=headers) as response:
            assert response.status_code == 200
            lines = response.aiter_lines()

            handle = start_run(RunTrigger.CHAT_TURN)
            try:
                end_run(handle, status=RunStatus.OK)
                event = await asyncio.wait_for(_next_event(lines), timeout=5)
            finally:
                cleanup_run(handle.run_id)

            assert event["id"] == str(handle.run_id)
            assert event["status"] == RunStatus.OK.value


@pytest.mark.asyncio
async def test_an_idle_stream_emits_keepalives(client, db_session, monkeypatch):
    """A proxy will close a stream that says nothing for long enough, and
    admin activity is bursty by nature. The interval is read from the
    module at await time, so shortening it here exercises the real timeout
    branch rather than a stand-in."""
    from app.admin import router as admin_router
    from app.main import app

    _user, headers = _admin(client, db_session)
    monkeypatch.setattr(admin_router, "_KEEPALIVE_SECONDS", 0.05)

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/admin/runs/stream", headers=headers) as response:
            assert response.status_code == 200

            async def _first_keepalive() -> str:
                async for line in response.aiter_lines():
                    if line.startswith(":"):
                        return line
                raise AssertionError("stream ended before a keepalive arrived")

            assert await asyncio.wait_for(_first_keepalive(), timeout=5) == ": keepalive"


@pytest.mark.asyncio
async def test_a_dropped_subscriber_ends_the_stream_instead_of_raising():
    """Regression test for the `except broker.SubscriberDropped: return`
    clause, asserted on the generator rather than over HTTP.

    Over HTTP this distinction is invisible, which is worth recording
    because it is counter-intuitive and was measured, not assumed:
    app/main.py installs a `@app.middleware("http")`, i.e. Starlette's
    BaseHTTPMiddleware, which runs the downstream app as its own task and
    closes the client-facing body when that task ends -- whether it ended
    by returning or by raising. So a client sees an orderly end of stream
    either way, and the HTTP-level test below cannot tell a handled drop
    from an exception escaping into the server logs.

    Driving `response.body_iterator` directly can: with the except clause
    the iterator is simply exhausted, and without it SubscriberDropped
    comes out here. Verified in both directions.

    Overflows the broker's 100-slot queue with a burst of publishes and no
    `await` in between, so the generator -- parked on subscription.get()
    after its first yield -- gets no chance to drain before it overflows.
    """
    from app.admin.router import admin_runs_stream

    # The endpoint's body never touches `principal`; it is there for the
    # require_role dependency, which authorization is tested separately.
    response = await admin_runs_stream(principal=None)
    iterator = response.body_iterator.__aiter__()

    # Starting the generator is what subscribes it, so it has to run up to
    # its first await before the burst -- publishing to a channel nobody is
    # subscribed to is a no-op and would leave nothing to drop.
    first = asyncio.ensure_future(iterator.__anext__())
    await asyncio.sleep(0)
    assert broker.subscriber_count(broker.ADMIN_RUNS_CHANNEL) == 1

    for i in range(150):
        broker.publish(broker.ADMIN_RUNS_CHANNEL, {"type": "run_finished", "id": str(i)})

    delivered = [await asyncio.wait_for(first, timeout=5)]
    with pytest.raises(StopAsyncIteration):
        while True:
            delivered.append(await asyncio.wait_for(iterator.__anext__(), timeout=5))

    assert len(delivered) == 100, "should deliver exactly what was buffered before the drop"
    assert broker.subscriber_count(broker.ADMIN_RUNS_CHANNEL) == 0, (
        "the subscription must be released when the stream closes"
    )


@pytest.mark.asyncio
async def test_a_dropped_subscribers_client_still_sees_a_terminated_stream(client, db_session):
    """The same drop, end to end. This asserts what the CLIENT observes --
    the events buffered before the drop, and then an end of stream rather
    than a wedge -- which is the user-visible contract.

    It does NOT pin the except clause: see the test above for why
    BaseHTTPMiddleware makes an escaping exception look identical from
    here. Both tests are kept because they cover different failures: this
    one would catch a stream that stops producing without closing.
    """
    from app.main import app

    _user, headers = _admin(client, db_session)

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/admin/runs/stream", headers=headers) as response:
            assert response.status_code == 200

            for i in range(150):
                broker.publish(broker.ADMIN_RUNS_CHANNEL, {"type": "run_finished", "id": str(i)})

            async def _drain() -> int:
                count = 0
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        count += 1
                return count

            # Completing at all is half the assertion: the stream ended
            # rather than stalling with the client still reading.
            delivered = await asyncio.wait_for(_drain(), timeout=5)
            assert delivered == 100, "the client should receive what was buffered before the drop"


def test_the_stream_endpoint_declares_no_request_scoped_session():
    """The rule itself, stated where a future edit will trip over it:
    /runs/stream must not take a `db: DbSession`.

    This exists alongside the pool measurement below because the two catch
    different halves of the regression. Measured, not assumed: adding an
    UNUSED `db: DbSession` does not move the pool count at all, because a
    SQLAlchemy Session checks a connection out lazily, on its first
    statement. The measurement below therefore only fires once something
    actually reads through the session -- by which point the parameter has
    already been there for a while. This catches it the moment it appears.
    """
    import inspect

    from app.admin.router import admin_runs_stream
    from app.deps import DbSession

    annotations = {
        name: parameter.annotation
        for name, parameter in inspect.signature(admin_runs_stream).parameters.items()
    }
    assert DbSession not in annotations.values(), (
        f"/runs/stream must hold no request-scoped session, got {annotations}"
    )


@pytest.mark.asyncio
async def test_an_open_stream_holds_no_pooled_connection():
    """The Phase 6 lesson, pinned by measurement. FastAPI tears the
    dependency stack down only after the response completes, which for SSE
    means when the client disconnects -- so a session that this endpoint
    has read through would sit `idle in transaction` for the life of the
    stream, and about fifteen such streams exhaust pool_size=5 +
    max_overflow=10, after which login, chat and approvals all block.

    Deliberately does NOT use the `client` fixture: that fixture overrides
    get_db to hand back the test's own session, which would satisfy a
    `db: DbSession` dependency out of a connection this test is already
    counting and make the measurement vacuous. Without the override, get_db
    draws from the real pool.

    Verified load-bearing by adding `db: DbSession` plus a `select 1`
    through it: this assertion then fails. Note the limit stated in the
    signature test above -- an unused session does not trip it.
    """
    from app.main import app

    assert not app.dependency_overrides, "an override would make this measurement vacuous"

    _user_id, username = _hard_committed_admin()
    pool = get_engine().pool

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        login = await ac.post(
            "/api/auth/login", json={"username": username, "password": "Passw0rd!dev"},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        baseline = pool.checkedout()
        async with ac.stream("GET", "/api/admin/runs/stream", headers=headers) as response:
            assert response.status_code == 200
            lines = response.aiter_lines()

            # Reading one event proves the generator has reached its await --
            # measuring before that could catch it mid-setup and pass by luck.
            broker.publish(broker.ADMIN_RUNS_CHANNEL, {"type": "run_finished", "id": "parked"})
            assert (await asyncio.wait_for(_next_event(lines), timeout=5))["id"] == "parked"

            assert pool.checkedout() == baseline, (
                "the open stream is holding a pooled connection: "
                f"{pool.checkedout()} checked out vs a baseline of {baseline}"
            )
