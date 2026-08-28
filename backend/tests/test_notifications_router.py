from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import httpx
import pytest

from app.db.models import Notification, NotificationType, Role, User
from app.db.session import get_sessionmaker
from app.notifications import broker, service

# /api/notifications/stream deliberately takes NO `db` dependency: a
# dependency-provided session stays checked out and `idle in transaction`
# for the whole life of an SSE response, which ends only when the client
# disconnects (see the endpoint's docstring). It opens its own short-lived
# session for the backlog read instead -- which means the `client` fixture's
# get_db override no longer reaches it, and rows that exist only inside a
# test's db_session SAVEPOINT are invisible to that read.
#
# The two tests below that assert on a REPLAYED BACKLOG therefore create
# their User and Notification rows through a real, hard-committing session,
# the same pattern (and for the same cross-connection reason) as
# tests/test_approvals_service.py and tests/test_admin_approvals_router.py.
# The other stream tests do not assert on a backlog, so an empty one is
# fine and they keep using db_session.
_hard_committed_user_ids: list[uuid.UUID] = []


@pytest.fixture(scope="module", autouse=True)
def _sweep_hard_committed_rows_after_module():
    """Deletes what `_hard_committed_login` and `_hard_committed_notification`
    commit, once every test in this module has finished and released its own
    db_session. Ids are registered the instant they are committed, before
    anything that could fail, so a test that blows up mid-way still gets its
    rows swept; the sweep itself runs on `yield`'s far side, which pytest
    reaches on failure paths too."""
    yield
    Session = get_sessionmaker()
    try:
        with Session() as session:
            if _hard_committed_user_ids:
                session.query(Notification).filter(
                    Notification.user_id.in_(_hard_committed_user_ids),
                ).delete(synchronize_session=False)
                session.query(User).filter(
                    User.id.in_(_hard_committed_user_ids),
                ).delete(synchronize_session=False)
            session.commit()
    finally:
        _hard_committed_user_ids.clear()


def _hard_committed_login(client, *, username: str, role: Role = Role.EMPLOYEE) -> tuple[uuid.UUID, dict]:
    """`_login`'s hard-committing twin, for tests whose rows must be visible
    from a connection other than the test's own. The username carries a
    random suffix because these rows really do land in the database: a
    fixed one would collide with itself if a previous run's sweep never
    got to finish."""
    from app.auth.security import hash_password

    unique = f"{username}-{uuid.uuid4().hex[:8]}"
    Session = get_sessionmaker()
    with Session() as hard_session:
        user = User(
            username=unique, email=f"{unique}@northstar.example", full_name=username.title(),
            password_hash=hash_password("Passw0rd!dev"), role=role,
        )
        hard_session.add(user)
        hard_session.commit()
        user_id = user.id
    _hard_committed_user_ids.append(user_id)

    resp = client.post("/api/auth/login", json={"username": unique, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user_id, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _hard_committed_notification(user_id: uuid.UUID, *, title: str) -> uuid.UUID:
    Session = get_sessionmaker()
    with Session() as hard_session:
        row = service.notify(
            hard_session, user_id=user_id, type=NotificationType.TICKET_CREATED, title=title, body="b",
        )
        hard_session.commit()
        return row.id


def _login(client, db_session, *, username: str, role: Role = Role.EMPLOYEE) -> tuple[User, dict]:
    """Real login through the API, mirroring test_tickets_router.py's
    _login helper -- this project has no auth_headers_for_role /
    current_user_for_role fixtures, so tests get both the User row (to
    address notifications to) and the auth headers this same way."""
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


def _guest_login(client) -> dict:
    """Matches test_chat_router.py's _guest_login helper exactly -- a guest
    principal has kind='guest' and user_id=None (app/auth/router.py's
    guest_login), which is what _require_user rejects."""
    resp = client.post("/api/auth/guest", json={"name": "Visitor", "email": "visitor@example.com"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _make_other_user(db_session) -> uuid.UUID:
    """notifications.user_id is a NOT NULL FK to users.id -- a bare
    uuid.uuid4() (as the brief's own draft used) trips ForeignKeyViolation.
    Same fix as test_tickets_router.py::_make_other_user: a real, unrelated
    User row through db_session so it lands in the same transaction."""
    other_id = uuid.uuid4()
    db_session.add(User(
        id=other_id, username=f"other-{other_id}", email=f"other-{other_id}@northstar.example",
        full_name="Other User", password_hash="x", role=Role.EMPLOYEE,
    ))
    db_session.commit()
    return other_id


def test_listing_returns_this_users_notifications_only(client, db_session):
    mine, headers = _login(client, db_session, username="notif-listing")
    other = _make_other_user(db_session)
    service.notify(db_session, user_id=mine.id, type=NotificationType.TICKET_CREATED, title="Mine", body="b")
    service.notify(db_session, user_id=other, type=NotificationType.TICKET_CREATED, title="Theirs", body="b")
    db_session.commit()

    body = client.get("/api/notifications", headers=headers).json()
    titles = [n["title"] for n in body]
    assert "Mine" in titles
    assert "Theirs" not in titles


def test_marking_read_sets_read_at(client, db_session):
    mine, headers = _login(client, db_session, username="notif-mark-read")
    row = service.notify(db_session, user_id=mine.id, type=NotificationType.TICKET_RESOLVED, title="T", body="b")
    db_session.commit()

    assert client.post(f"/api/notifications/{row.id}/read", headers=headers).status_code == 200
    remaining = client.get("/api/notifications?unread_only=true", headers=headers).json()
    assert [n["id"] for n in remaining] == []


def test_marking_someone_elses_notification_is_404(client, db_session):
    _mine, headers = _login(client, db_session, username="notif-not-mine")
    other = _make_other_user(db_session)
    row = service.notify(db_session, user_id=other, type=NotificationType.TICKET_RESOLVED, title="T", body="b")
    db_session.commit()
    response = client.post(f"/api/notifications/{row.id}/read", headers=headers)
    assert response.status_code == 404


def test_guest_cannot_list_notifications(client):
    """notifications.user_id is NOT NULL and a guest is not a row in
    `users` (spec 5.1) -- _require_user must reject before any query runs."""
    headers = _guest_login(client)
    assert client.get("/api/notifications", headers=headers).status_code == 403


def test_guest_cannot_mark_a_notification_read(client):
    headers = _guest_login(client)
    assert client.post(f"/api/notifications/{uuid.uuid4()}/read", headers=headers).status_code == 403


def test_guest_cannot_open_the_stream(client):
    """_require_user raises before the endpoint ever builds a
    StreamingResponse, so this is a normal, fast request -- no need for the
    ASGI-level harness the live-stream tests below use."""
    headers = _guest_login(client)
    assert client.get("/api/notifications/stream", headers=headers).status_code == 403


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
    """httpx 0.28's stock ASGITransport (httpx/_transports/asgi.py) does
    `await self.app(scope, receive, send)` and only builds the Response
    object after that await returns -- it collects every response.body
    message into a list first. That makes it unusable for these tests: the
    notification stream's generator never finishes on its own (it loops
    forever emitting keepalives until the client disconnects), so the
    stock transport's `await self.app(...)` would never return and
    `ac.stream(...)` would hang forever -- confirmed empirically before
    writing this class, not a guess.

    This transport instead runs the ASGI app as a background task and
    streams `http.response.body` chunks out through a queue as they
    arrive, so `handle_async_request` can return a Response as soon as
    `http.response.start` shows up, while the app keeps running
    underneath. That is what actually lets a test interleave a read of
    the stream with a `broker.publish(...)` call, which is the entire
    point of these tests (TestClient's synchronous iteration can't do this
    either).

    Deliberately does NOT normalize an app exception into a clean end of
    body. A real ASGI server does not send a tidy final `more_body: False`
    message when the app raises mid-stream -- the connection just stops
    producing data. If this transport pushed a closing `None` sentinel on
    any exception, test_a_dropped_subscriber_closes_the_stream_instead_of_hanging
    below would pass whether or not the router actually closes the stream
    on SubscriberDropped, because the harness itself would paper over the
    difference. Instead an app exception leaves the body queue exactly as
    it was -- a reader waiting on the next chunk hangs, same as against a
    real server, and callers are expected to bound their reads with
    asyncio.wait_for (as every test below does).
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
                # Nothing more to give an app that keeps calling receive()
                # after the request body is exhausted (this endpoint never
                # does, but a well-behaved receive() must not raise here).
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
                # See the class docstring: deliberately no closing `None`
                # here. Only unblock a caller still waiting on headers that
                # never came; a reader already past that point must hang,
                # same as it would against a real server that died
                # mid-stream, so tests can tell a clean close from a wedge.
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


@pytest.mark.asyncio
async def test_the_stream_replays_unread_rows_then_delivers_live_events(client):
    """TestClient cannot do this: its synchronous iteration cannot interleave
    a publish with a read. This test drives the ASGI app through
    _LiveASGITransport instead of the stock httpx.ASGITransport, which
    cannot stream an endpoint that never finishes on its own -- see that
    class's docstring for why.

    Reuses the `client` fixture only to log in and to get its dependency
    override (app.db.session.get_db -> db_session) installed on the shared
    `app` object -- the actual streaming happens over a second, custom
    transport-backed async client against that same app. `AsyncClient`'s
    own `__aexit__` closes `transport`, so there is no separate
    `transport.aclose()` call to make here.
    """
    from app.main import app

    user_id, headers = _hard_committed_login(client, username="notif-stream")
    _hard_committed_notification(user_id, title="Backlog")

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/notifications/stream", headers=headers) as response:
            assert response.status_code == 200
            lines = response.aiter_lines()

            replayed = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert replayed["title"] == "Backlog"

            broker.publish(user_id, {"type": "ticket_assigned", "id": str(uuid.uuid4()), "title": "Live", "body": "b"})
            live = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert live["title"] == "Live"


@pytest.mark.asyncio
async def test_a_replayed_and_published_event_with_the_same_id_arrives_once(client):
    """Regression test for the router's `seen` dedup set. Verified
    load-bearing by deleting the dedup logic and confirming this test then
    fails (see task-8-report.md for the exact before/after run): without
    it, the id-colliding publish below would arrive as a second frame
    before the sentinel, instead of being swallowed.

    `seen` is now seeded from the backlog and never added to afterwards
    (the set used to grow for the life of the stream), so this test is also
    what pins the only overlap dedup has to cover: a row that is both
    replayed from the database and published live."""
    from app.main import app

    user_id, headers = _hard_committed_login(client, username="notif-dedup")
    row_id = _hard_committed_notification(user_id, title="Backlog")

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/notifications/stream", headers=headers) as response:
            assert response.status_code == 200
            lines = response.aiter_lines()

            replayed = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert replayed["id"] == str(row_id)

            # Same id as the row just replayed: a duplicate that dedup must swallow.
            broker.publish(user_id, {"type": "ticket_created", "id": str(row_id), "title": "Duplicate", "body": "b"})
            # A distinct event published right after it. If dedup is working,
            # this -- not a second copy of "Duplicate" -- is the next frame.
            broker.publish(user_id, {"type": "ticket_assigned", "id": str(uuid.uuid4()), "title": "Sentinel", "body": "b"})

            next_event = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert next_event["title"] == "Sentinel"


@pytest.mark.asyncio
async def test_a_dropped_subscriber_closes_the_stream_instead_of_hanging(client, db_session):
    """Regression test for the `except broker.SubscriberDropped: return`
    clause. Verified load-bearing by deleting that clause and confirming
    this test then fails with a timeout (see task-8-report.md): without
    it, the SubscriberDropped raised once the 100 buffered events are
    drained propagates out of the generator uncaught, and
    _LiveASGITransport deliberately does not fake a clean end of body for
    that case (see its docstring) -- so the stream just hangs instead of
    closing, and the bounded read below times out instead of completing.

    Overflows the broker's default 100-slot queue (see
    app/notifications/broker.py's _DEFAULT_MAX_QUEUE) with a burst of
    publishes and no `await` in between, so the background app task --
    already parked on `subscription.get()` by the time `ac.stream(...)`
    returns (see _LiveASGITransport's docstring on header/body ordering)
    -- gets no chance to drain the queue before it overflows.
    """
    from app.main import app

    mine, headers = _login(client, db_session, username="notif-dropped")

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/notifications/stream", headers=headers) as response:
            assert response.status_code == 200

            for i in range(150):
                broker.publish(mine.id, {"type": "ticket_created", "id": str(i), "title": f"n{i}", "body": "b"})

            lines = response.aiter_lines()
            received: list[dict] = []

            async def _drain():
                async for line in lines:
                    if line.startswith("data: "):
                        received.append(json.loads(line[len("data: "):]))

            await asyncio.wait_for(_drain(), timeout=5)
            assert len(received) == 100


@pytest.mark.asyncio
async def test_a_notification_published_during_the_backlog_read_is_not_lost(client, db_session, monkeypatch):
    """Regression test for subscribe-before-read (spec's reasoning, fixed
    this round: the code originally read the backlog in the endpoint body,
    before the generator -- and therefore before broker.subscribe -- ever
    ran). Verified load-bearing by reverting the router to read-before-
    subscribe and confirming this test then fails (see task-8-report.md):
    with the read happening first, the event this test publishes would be
    published to zero subscribers and lost for good, since it is never
    written to the notifications table either -- exactly the race the fix
    closes.

    Monkeypatches app.notifications.router's `service.list_for_user` (the
    call the stream endpoint makes for its backlog) so that call itself
    publishes an event before returning the real, empty backlog --
    simulating a notification whose broker.publish lands *during* the
    read. With subscribe-before-read this event is already queued by the
    time that read happens, and reaches the client.
    """
    from app.main import app
    from app.notifications import router as notifications_router

    mine, headers = _login(client, db_session, username="notif-race")

    real_list_for_user = service.list_for_user
    already_published = False

    def _list_for_user_that_races_a_publish(db, user_id, *, unread_only=False):
        nonlocal already_published
        if not already_published:
            already_published = True
            broker.publish(user_id, {"type": "ticket_created", "id": "raced-event", "title": "Raced", "body": "b"})
        return real_list_for_user(db, user_id, unread_only=unread_only)

    monkeypatch.setattr(notifications_router.service, "list_for_user", _list_for_user_that_races_a_publish)

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/notifications/stream", headers=headers) as response:
            assert response.status_code == 200
            lines = response.aiter_lines()
            event = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert event["title"] == "Raced"


@pytest.mark.asyncio
async def test_an_open_stream_holds_no_database_connection():
    """Finding 3. `stream_notifications` used to take a `db: DbSession`
    dependency and read the backlog inside the generator. FastAPI exits the
    dependency stack only once the response is complete, and an SSE response
    completes when the client disconnects -- so the session stayed checked
    out, and (having read) `idle in transaction`, for as long as the client
    stayed connected. Measured against real uvicorn: three open streams,
    three such backends. pool_size=5 + max_overflow=10 means roughly fifteen
    concurrent streams exhaust the pool and every other request blocks and
    then fails, and the open snapshots hold xmin back so VACUUM reclaims
    nothing.

    Deliberately does NOT use the `client` fixture. That fixture overrides
    get_db to hand back the test's own already-checked-out session, which
    would mask the leak entirely -- the buggy endpoint would borrow a
    connection the baseline already counted. Everything here goes through
    the real get_db, against hard-committed rows.

    Reads one backlog event before measuring, which is the synchronisation
    point: the generator cannot have emitted it without having finished the
    backlog read, so by then the session must already be closed.
    """
    from app.db.session import get_engine
    from app.main import app

    engine = get_engine()

    user_id, headers = _hard_committed_stream_login(username="notif-pool")
    _hard_committed_notification(user_id, title="Backlog")

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        baseline = engine.pool.checkedout()
        async with ac.stream("GET", "/api/notifications/stream", headers=headers) as response:
            assert response.status_code == 200
            lines = response.aiter_lines()
            replayed = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert replayed["title"] == "Backlog"

            # The stream is open and has already served its backlog. If it
            # still owned a session, this would be baseline + 1.
            assert engine.pool.checkedout() == baseline, (
                "the open SSE stream is pinning a pooled connection"
            )


def _hard_committed_stream_login(*, username: str, role: Role = Role.EMPLOYEE) -> tuple[uuid.UUID, dict]:
    """`_hard_committed_login` without the `client` fixture, whose get_db
    override is exactly what the pool test above must avoid installing.
    Mints the access token through the same `create_access_token` the login
    endpoint uses rather than replaying the HTTP login, since the point
    under test is the stream endpoint, not auth."""
    from app.auth.security import create_access_token, hash_password

    unique = f"{username}-{uuid.uuid4().hex[:8]}"
    Session = get_sessionmaker()
    with Session() as hard_session:
        user = User(
            username=unique, email=f"{unique}@northstar.example", full_name=username.title(),
            password_hash=hash_password("Passw0rd!dev"), role=role,
        )
        hard_session.add(user)
        hard_session.commit()
        user_id = user.id
    _hard_committed_user_ids.append(user_id)

    token = create_access_token({
        "kind": "user", "user_id": str(user_id), "role": role.value, "sub": unique,
    })
    return user_id, {"Authorization": f"Bearer {token}"}


async def _next_event(lines) -> dict:
    """Skips SSE keepalive comments (lines beginning with ':') and blanks."""
    async for line in lines:
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError("stream ended before an event arrived")
