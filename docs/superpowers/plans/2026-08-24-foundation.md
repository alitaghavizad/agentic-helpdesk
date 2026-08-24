# Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a working backend: repo scaffold, full Postgres schema, config with boot validation, bcrypt+JWT auth, RBAC retrieval-filter policy, and an idempotent seed of 126 accounts (admin + 100 employees + 25 helpdesk) from `corporate_rag_dataset/`.

**Architecture:** FastAPI backend in `backend/app/`, SQLAlchemy 2.x models with Alembic migrations against the `ticketing` Postgres database, stateless JWT auth (claims carry the full `Principal`), and a pure-function RBAC policy module with no I/O.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, Alembic, psycopg3, pydantic-settings, PyJWT, bcrypt, pytest, uv.

## Global Constraints

- Backend: Python 3.13, FastAPI, SQLAlchemy 2.x, Alembic (spec D1).
- Database `ticketing` only — the pre-existing `mydb` on the same Postgres instance is never touched (spec §3.2).
- Chroma is already running and published at `http://localhost:8000` — not used in this plan (spec §3.2, done).
- JWT: HS256, 15-minute access token, httpOnly + `SameSite=Strict` refresh cookie (spec §12.5).
- All password hashing via bcrypt (spec §12.5).
- No raw SQL string interpolation anywhere — every query goes through the SQLAlchemy ORM, parameterized (spec §12.5).
- Auth failure responses are generic and never distinguish "no such user" from "wrong password" (spec §12.5).
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` default to `admin`/`admin`; `SEED_USER_PASSWORD` defaults to `Passw0rd!dev`; both `.env`-overridable (spec §3.3).
- Backend binds `127.0.0.1` by default (spec §3.3).
- All timestamps `TIMESTAMPTZ`; all ids UUID v4, except `tickets.ticket_number` / `approval_requests.request_number` which are serial integers (spec §5).
- Model id `claude-opus-5` is a global constraint for later phases; not exercised in this plan.

**Scope note:** This plan is backend-only. A frontend login page arrives in the next plan (RAG + chat), since spec §18's own phase breakdown has no frontend deliverable until chat exists to log into.

**Two implementation-level gap-fills made in this plan** (the spec's data model was silent on both; neither changes any documented behavior):
1. `users.full_name` — the spec's `users` table (§5.1) has no name field, only `username`/`email`, which is unusable for a UI. Added as a plain display column.
2. `notifications` table columns — spec §5 lists `notifications` only as "in-app feed, SSE-pushed" without column definitions. Concrete columns are defined in Task 3, consistent with the triggers spec §10 describes. The `notifications` router/broker itself is out of scope for this plan (built in the Approvals-phase plan) — only the table exists here.

---

### Task 1: Repo scaffold — compose reference, Makefile, backend package

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `Makefile`
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`

**Interfaces:**
- Produces: a `uv`-managed virtualenv at `backend/.venv` with `fastapi`, `sqlalchemy`, `alembic`, `psycopg[binary]`, `pydantic-settings`, `bcrypt`, `pyjwt`, `python-multipart` installed, plus `pytest`, `pytest-cov`, `httpx` as dev dependencies. Every later task runs via `cd backend && uv run ...`.

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
# Reference compose file for reproducing the dev environment from scratch.
# NOTE: In this project's current dev session, `postgres18` and `chroma`
# were already running as manually-created containers before this file
# existed. `make db-up` therefore health-checks those containers rather
# than invoking `docker compose up`. Use this file if you tear the
# environment down and want to bring it back up from a clean slate.
services:
  postgres18:
    image: postgres:18
    container_name: postgres18
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: "123"
      POSTGRES_DB: mydb
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/18/docker

  chroma:
    image: ghcr.io/chroma-core/chroma:1.0.0
    container_name: chroma
    ports:
      - "8000:8000"
    volumes:
      - chroma_data:/data

volumes:
  postgres_data:
  chroma_data:
```

- [ ] **Step 2: Create `.env.example`**

```
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash

DATABASE_URL=postgresql+psycopg://postgres:123@localhost:5432/ticketing
CHROMA_URL=http://localhost:8000
CHROMA_BACKEND=mcp

SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=

JWT_SECRET=changeme-generate-a-real-secret

ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin
SEED_USER_PASSWORD=Passw0rd!dev

MAX_COST_PER_CONVERSATION_USD=0.50
MAX_TOOL_ITERATIONS=12

AGENT_ENABLED=true
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8080
FRONTEND_ORIGIN=http://localhost:5173
```

- [ ] **Step 3: Create `Makefile`**

```makefile
.PHONY: db-up migrate seed dev test

db-up:
	@echo "Checking Postgres..."
	@docker exec postgres18 pg_isready -U postgres || (echo "Postgres not reachable" && exit 1)
	@echo "Checking Chroma..."
	@curl -sf http://localhost:8000/api/v2/heartbeat > /dev/null || (echo "Chroma not reachable" && exit 1)
	@echo "Both services healthy."

migrate:
	cd backend && uv run alembic upgrade head

seed:
	cd backend && uv run python -m app.db.seed

dev:
	cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8080

test:
	cd backend && uv run pytest -v
```

- [ ] **Step 4: Create `backend/pyproject.toml`**

```toml
[project]
name = "ticketing-backend"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0.36",
    "alembic>=1.14",
    "psycopg[binary]>=3.2",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "bcrypt>=4.2",
    "pyjwt>=2.9",
    "python-multipart>=0.0.12",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-cov>=6.0",
    "httpx>=0.27",
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

- [ ] **Step 5: Create `backend/app/__init__.py`** (empty file)

- [ ] **Step 6: Install dependencies and verify**

Run: `cd backend && uv sync`
Expected: completes without error, creates `backend/.venv` and `backend/uv.lock`.

Run: `cd backend && uv run python -c "import fastapi, sqlalchemy, alembic, jwt, bcrypt; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 7: Verify `make db-up`**

Run: `make db-up`
Expected: prints "Both services healthy." (Postgres and Chroma are already running per the current session.)

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml .env.example Makefile backend/pyproject.toml backend/app/__init__.py backend/uv.lock
git commit -m "Scaffold repo: compose reference, Makefile, backend package"
```

---

### Task 2: Config module with boot validation

**Files:**
- Create: `backend/app/config.py`
- Test: `backend/tests/test_config.py`
- Test: `backend/tests/__init__.py` (empty)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `get_settings() -> Settings` (cached singleton), `class Settings(BaseSettings)` with all fields listed in the Global Constraints section, `class ConfigError(RuntimeError)`. Every later task that needs configuration calls `get_settings()`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/__init__.py` (empty), then `backend/tests/test_config.py`:

```python
import pytest

from app.config import ConfigError, Settings


def _base_kwargs(**overrides):
    kwargs = dict(
        anthropic_api_key="sk-ant-test",
        database_url="postgresql+psycopg://postgres:123@localhost:5432/ticketing",
        jwt_secret="a-real-secret-value",
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_config_passes_boot_validation():
    settings = Settings(**_base_kwargs())
    settings.validate_boot()  # must not raise


def test_missing_anthropic_key_fails_boot():
    settings = Settings(**_base_kwargs(anthropic_api_key=""))
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        settings.validate_boot()


def test_default_jwt_secret_fails_boot():
    settings = Settings(**_base_kwargs(jwt_secret="changeme-generate-a-real-secret"))
    with pytest.raises(ConfigError, match="JWT_SECRET"):
        settings.validate_boot()


def test_empty_jwt_secret_fails_boot():
    settings = Settings(**_base_kwargs(jwt_secret=""))
    with pytest.raises(ConfigError, match="JWT_SECRET"):
        settings.validate_boot()


def test_get_settings_loads_real_env_file_and_is_cached():
    from app.config import get_settings

    settings_a = get_settings()
    settings_b = get_settings()
    assert settings_a is settings_b
    assert settings_a.anthropic_api_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: FAIL/ERROR — `app.config` does not exist yet.

- [ ] **Step 3: Write `backend/app/config.py`**

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class ConfigError(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    database_url: str = ""
    chroma_url: str = "http://localhost:8000"
    chroma_backend: str = "mcp"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    jwt_secret: str = ""

    admin_username: str = "admin"
    admin_password: str = "admin"
    seed_user_password: str = "Passw0rd!dev"

    max_cost_per_conversation_usd: float = 0.50
    max_tool_iterations: int = 12

    agent_enabled: bool = True
    backend_host: str = "127.0.0.1"
    backend_port: int = 8080
    frontend_origin: str = "http://localhost:5173"

    def validate_boot(self) -> None:
        missing = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.database_url:
            missing.append("DATABASE_URL")
        if not self.jwt_secret or self.jwt_secret == "changeme-generate-a-real-secret":
            missing.append("JWT_SECRET (unset or still the example placeholder)")
        if missing:
            raise ConfigError(
                "Missing or invalid required configuration: " + ", ".join(missing)
            )
        if self.admin_password == "admin":
            print(
                "WARNING: ADMIN_PASSWORD is the default 'admin'. "
                "This is insecure outside local development.",
                flush=True,
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_boot()
    return settings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: PASS (5 tests). The last test requires the repo-root `.env` (already populated with real keys) to be present.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/__init__.py backend/tests/test_config.py
git commit -m "Add config module with boot-time validation"
```

---

### Task 3: Database models + Alembic migration + base test fixtures

**Files:**
- Create: `backend/app/db/__init__.py` (empty)
- Create: `backend/app/db/models.py`
- Create: `backend/app/db/session.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_db_schema.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 2.
- Produces: `Base` (declarative base) and every ORM class in `app.db.models` (`User`, `RefreshToken`, `Conversation`, `Message`, `Attachment`, `Task`, `Ticket`, `ApprovalRequest`, `OutboundEmail`, `Run`, `Span`, `AuditLog`, `UsageCounter`, `Lesson`, `Notification`) plus enums (`Role`, `Clearance`, `EscalationAuthority`, etc. — full list in Step 3). `get_engine() -> Engine`, `get_sessionmaker() -> sessionmaker`, `get_db() -> Generator[Session, None, None]` from `app.db.session`. Test fixtures `db_session` and the autouse `_migrated_database` from `backend/tests/conftest.py`, used by every later task's DB-touching tests.

- [ ] **Step 1: Create `backend/app/db/__init__.py`** (empty file)

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_db_schema.py`:

```python
import uuid

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.models import Conversation
from app.db.session import get_engine, get_sessionmaker

EXPECTED_TABLES = {
    "users", "refresh_tokens", "conversations", "messages", "attachments",
    "tasks", "tickets", "approval_requests", "outbound_emails",
    "runs", "spans", "audit_log", "usage_counters", "lessons", "notifications",
}


def test_all_tables_exist_after_migration():
    inspector = sa.inspect(get_engine())
    tables = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables after migration: {missing}"


def test_conversations_requester_constraint_rejects_empty_requester():
    Session = get_sessionmaker()
    with Session() as session:
        session.execute(
            Conversation.__table__.insert().values(id=uuid.uuid4(), status="active")
        )
        try:
            session.commit()
            assert False, "expected IntegrityError for missing requester"
        except IntegrityError:
            session.rollback()
```

This test cannot pass yet — `app.db.models` and `app.db.session` don't exist, and even once they do, no migration has created the tables.

- [ ] **Step 3: Write `backend/app/db/models.py`**

```python
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, Identity, Integer, Numeric,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ---- Enums ------------------------------------------------------------------

class Role(str, enum.Enum):
    GUEST = "guest"
    EMPLOYEE = "employee"
    HELPDESK = "helpdesk"
    ADMIN = "admin"


class Clearance(str, enum.Enum):
    STANDARD = "standard"
    SENSITIVE = "sensitive"
    PRIVILEGED = "privileged"


class EscalationAuthority(str, enum.Enum):
    STANDARD = "standard"
    HIGH = "high"


class ConversationStatus(str, enum.Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class AttachmentKind(str, enum.Enum):
    IMAGE = "image"
    PDF = "pdf"
    AUDIO = "audio"


class ParseStatus(str, enum.Enum):
    PENDING = "pending"
    PARSED = "parsed"
    FAILED = "failed"
    REJECTED = "rejected"


class TaskCategory(str, enum.Enum):
    ACCESS_REQUEST = "access_request"
    AUTHENTICATION_MFA = "authentication_mfa"
    VPN_NETWORK = "vpn_network"
    DEVICE_ENDPOINT = "device_endpoint"
    APPLICATION_PERMISSION = "application_permission"
    EMAIL_COLLABORATION = "email_collaboration"
    DEVELOPER_TOOLING = "developer_tooling"
    CLOUD_INFRASTRUCTURE = "cloud_infrastructure"
    DATABASE_ACCESS = "database_access"
    HR_SYSTEMS = "hr_systems"
    FINANCE_SYSTEMS = "finance_systems"
    SECURITY_INCIDENT = "security_incident"
    HARDWARE = "hardware"
    GENERAL_QUESTION = "general_question"
    OTHER = "other"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResolutionPath(str, enum.Enum):
    ANSWERED = "answered"
    TICKETED = "ticketed"
    ESCALATED = "escalated"
    PENDING = "pending"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    ESCALATED = "escalated"


class ApprovalActionType(str, enum.Enum):
    SEND_EMAIL = "send_email"
    GRANT_SYSTEM_ACCESS = "grant_system_access"
    RESET_CREDENTIAL = "reset_credential"
    UPDATE_USER_CLEARANCE = "update_user_clearance"
    DISCLOSE_RESTRICTED_INFORMATION = "disclose_restricted_information"
    CROSS_DEPARTMENT_TICKET_ASSIGNMENT = "cross_department_ticket_assignment"
    EXTERNAL_API_WRITE = "external_api_write"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"


class EmailStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


class RunTrigger(str, enum.Enum):
    CHAT_TURN = "chat_turn"
    DOSSIER = "dossier"
    REFLECTION = "reflection"
    INGEST_EVAL = "ingest_eval"


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    ABORTED = "aborted"


class SpanKind(str, enum.Enum):
    LLM = "llm"
    TOOL = "tool"
    MCP = "mcp"
    RETRIEVAL = "retrieval"
    GUARDRAIL = "guardrail"
    PARSE = "parse"
    EXECUTOR = "executor"
    DB = "db"


class SpanStatus(str, enum.Enum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"


class ActorType(str, enum.Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class LessonConfidence(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LessonStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class NotificationType(str, enum.Enum):
    APPROVAL_DECIDED = "approval_decided"
    TICKET_CREATED = "ticket_created"
    TICKET_ASSIGNED = "ticket_assigned"
    TICKET_STATUS_CHANGED = "ticket_status_changed"
    TICKET_RESOLVED = "ticket_resolved"
    ATTACHMENT_REQUESTED = "attachment_requested"


# ---- Identity -----------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="role"), nullable=False)
    clearance: Mapped[Clearance | None] = mapped_column(SAEnum(Clearance, name="clearance"), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employee_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    helpdesk_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    specialization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    escalation_authority: Mapped[EscalationAuthority | None] = mapped_column(
        SAEnum(EscalationAuthority, name="escalation_authority"), nullable=True
    )
    shift: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


# ---- Conversation ---------------------------------------------------------------

class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL) OR (guest_name IS NOT NULL AND guest_email IS NOT NULL)",
            name="ck_conversations_requester_present",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    guest_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guest_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[ConversationStatus] = mapped_column(
        SAEnum(ConversationStatus, name="conversation_status"), nullable=False,
        default=ConversationStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    role: Mapped[MessageRole] = mapped_column(SAEnum(MessageRole, name="message_role"), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    uploader_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    kind: Mapped[AttachmentKind] = mapped_column(SAEnum(AttachmentKind, name="attachment_kind"), nullable=False)
    parse_status: Mapped[ParseStatus] = mapped_column(
        SAEnum(ParseStatus, name="parse_status"), nullable=False, default=ParseStatus.PENDING
    )
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


# ---- Work -----------------------------------------------------------------------

class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    guest_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[TaskCategory] = mapped_column(SAEnum(TaskCategory, name="task_category"), nullable=False)
    severity: Mapped[Severity] = mapped_column(SAEnum(Severity, name="severity"), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    affected_systems: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    classified_by_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    resolution_path: Mapped[ResolutionPath] = mapped_column(
        SAEnum(ResolutionPath, name="resolution_path"), nullable=False, default=ResolutionPath.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticket_number: Mapped[int] = mapped_column(Integer, Identity(start=1), unique=True, nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), unique=True, nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    requester_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    requester_guest_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assignee_helpdesk_ref: Mapped[str] = mapped_column(String(32), nullable=False)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    matched_specialization: Mapped[str] = mapped_column(String(255), nullable=False)
    assignment_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    assignment_score: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    priority: Mapped[TicketPriority] = mapped_column(SAEnum(TicketPriority, name="ticket_priority"), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, name="ticket_status"), nullable=False, default=TicketStatus.OPEN
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = _uuid_pk()
    request_number: Mapped[int] = mapped_column(Integer, Identity(start=1), unique=True, nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    requester_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action_type: Mapped[ApprovalActionType] = mapped_column(
        SAEnum(ApprovalActionType, name="approval_action_type"), nullable=False
    )
    action_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(SAEnum(RiskLevel, name="risk_level"), nullable=False)
    agent_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus, name="approval_status"), nullable=False, default=ApprovalStatus.PENDING
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    execution_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class OutboundEmail(Base):
    __tablename__ = "outbound_emails"

    id: Mapped[uuid.UUID] = _uuid_pk()
    approval_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approval_requests.id"), nullable=False)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[EmailStatus] = mapped_column(
        SAEnum(EmailStatus, name="email_status"), nullable=False, default=EmailStatus.QUEUED
    )
    smtp_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


# ---- Observability ----------------------------------------------------------------

class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    trigger: Mapped[RunTrigger] = mapped_column(SAEnum(RunTrigger, name="run_trigger"), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status"), nullable=False, default=RunStatus.RUNNING
    )
    started_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Span(Base):
    __tablename__ = "spans"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_spans_run_sequence"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    parent_span_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("spans.id"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[SpanKind] = mapped_column(SAEnum(SpanKind, name="span_kind"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SpanStatus] = mapped_column(
        SAEnum(SpanStatus, name="span_status"), nullable=False, default=SpanStatus.OK
    )
    started_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_type: Mapped[ActorType] = mapped_column(SAEnum(ActorType, name="actor_type"), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(255), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class UsageCounter(Base):
    __tablename__ = "usage_counters"

    user_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)


# ---- Learning -------------------------------------------------------------------

class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    applies_to: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    confidence: Mapped[LessonConfidence] = mapped_column(
        SAEnum(LessonConfidence, name="lesson_confidence"), nullable=False
    )
    embedded_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[LessonStatus] = mapped_column(
        SAEnum(LessonStatus, name="lesson_status"), nullable=False, default=LessonStatus.ACTIVE
    )
    created_by_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


# ---- Notifications ----------------------------------------------------------------

class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    type: Mapped[NotificationType] = mapped_column(
        SAEnum(NotificationType, name="notification_type"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
```

- [ ] **Step 4: Write `backend/app/db/session.py`**

```python
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 5: Set up Alembic**

Run: `cd backend && uv run alembic init alembic`
Expected: creates `backend/alembic/`, `backend/alembic.ini`.

Replace the generated `backend/alembic.ini`'s `[alembic]` section's `script_location` line and add `prepend_sys_path`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
```

(Leave the rest of the generated `alembic.ini` — logging sections etc. — as `alembic init` created it.)

Replace the generated `backend/alembic/env.py` with:

```python
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 6: Generate and apply the baseline migration**

Run: `cd backend && uv run alembic revision --autogenerate -m "baseline schema"`
Expected: creates a file under `backend/alembic/versions/` containing `op.create_table(...)` calls for all 15 tables.

Open the generated file and confirm it contains a `create_table` call for every name in `EXPECTED_TABLES` (Step 2). If Alembic's autogenerate ordering fails on FK dependencies (unlikely — the schema is a DAG), reorder the `op.create_table` calls so tables are created after every table they reference.

Run: `cd backend && uv run alembic upgrade head`
Expected: completes without error; the `ticketing` database now has all 15 tables.

- [ ] **Step 7: Write `backend/tests/conftest.py`**

```python
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.session import get_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        check=True,
    )
    yield


@pytest.fixture()
def db_session():
    engine = get_engine()
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()
```

`join_transaction_mode="create_savepoint"` (SQLAlchemy 2.0+) lets test code call `session.commit()` freely — each commit only releases and reopens a SAVEPOINT, while the outer transaction (and therefore full test isolation via rollback) stays intact. Every later task's DB tests depend on this.

- [ ] **Step 8: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_db_schema.py -v`
Expected: PASS (2 tests).

- [ ] **Step 9: Commit**

```bash
git add backend/app/db/ backend/alembic.ini backend/alembic/ backend/tests/conftest.py backend/tests/test_db_schema.py
git commit -m "Add full database schema and Alembic baseline migration"
```

---

### Task 4: Auth security utilities — bcrypt + JWT

**Files:**
- Create: `backend/app/auth/__init__.py` (empty)
- Create: `backend/app/auth/security.py`
- Test: `backend/tests/test_security.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 2.
- Produces: `hash_password(plain: str) -> str`, `verify_password(plain: str, hashed: str) -> bool`, `create_access_token(claims: dict) -> str`, `create_refresh_token(*, subject: str) -> tuple[str, str, datetime]` (returns `(raw_token, sha256_hash, expires_at)`), `decode_token(token: str) -> dict`, `hash_refresh_token(raw: str) -> str`. Task 7's auth router calls all of these.

- [ ] **Step 1: Write the failing test**

Create `backend/app/auth/__init__.py` (empty), then `backend/tests/test_security.py`:

```python
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.auth.security import (
    create_access_token, create_refresh_token, decode_token,
    hash_password, hash_refresh_token, verify_password,
)
from app.config import get_settings


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_password_hash_is_salted_differently_each_time():
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b


def test_access_token_roundtrip():
    token = create_access_token({"sub": "user-123", "role": "employee", "kind": "user"})
    claims = decode_token(token)
    assert claims["sub"] == "user-123"
    assert claims["role"] == "employee"
    assert claims["type"] == "access"


def test_refresh_token_hash_matches_stored_hash():
    raw, token_hash, expires_at = create_refresh_token(subject="user-123")
    assert hash_refresh_token(raw) == token_hash
    claims = decode_token(raw)
    assert claims["type"] == "refresh"
    assert claims["exp"] > claims["iat"]
    assert expires_at.timestamp() == pytest.approx(claims["exp"], abs=2)


def test_tampered_token_is_rejected():
    token = create_access_token({"sub": "user-123", "role": "employee", "kind": "user"})
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(tampered)


def test_expired_token_is_rejected():
    now = datetime.now(timezone.utc)
    expired_payload = {
        "sub": "user-123", "role": "employee", "type": "access",
        "iat": now - timedelta(minutes=30), "exp": now - timedelta(minutes=15),
    }
    expired_token = jwt.encode(expired_payload, get_settings().jwt_secret, algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(expired_token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_security.py -v`
Expected: FAIL/ERROR — `app.auth.security` does not exist yet.

- [ ] **Step 3: Write `backend/app/auth/security.py`**

```python
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import get_settings

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=7)
JWT_ALGORITHM = "HS256"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(claims: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        **claims,
        "type": "access",
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=JWT_ALGORITHM)


def create_refresh_token(*, subject: str) -> tuple[str, str, datetime]:
    """Returns (raw_token, sha256_hash_for_storage, expires_at)."""
    now = datetime.now(timezone.utc)
    expires_at = now + REFRESH_TOKEN_TTL
    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),
    }
    raw = jwt.encode(payload, get_settings().jwt_secret, algorithm=JWT_ALGORITHM)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, token_hash, expires_at


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALGORITHM])


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_security.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth/__init__.py backend/app/auth/security.py backend/tests/test_security.py
git commit -m "Add bcrypt password hashing and JWT token utilities"
```

---

### Task 5: RBAC policy — clearance mapping + retrieval filter matrix

**Files:**
- Create: `backend/app/rbac/__init__.py` (empty)
- Create: `backend/app/rbac/policy.py`
- Test: `backend/tests/test_rbac_matrix.py`

**Interfaces:**
- Consumes: `Clearance`, `Role` enums from `app.db.models` (Task 3). Pure functions — no I/O, no DB session.
- Produces: `class Principal` (frozen dataclass: `kind`, `user_id`, `role`, `clearance`, `department`, `employee_ref`, `helpdesk_ref` — all `str | None` except `kind`/`role: str`), `class RetrievalDenied(PermissionError)`, `map_access_classification(raw: str) -> Clearance`, `map_escalation_authority(raw: str) -> str`, `retrieval_filter(principal: Principal, collection: str, *, helpdesk_visible_employee_ids: list[str] | None = None) -> dict`. Task 6's seed script calls the two `map_*` functions. Task 7's `deps.py` constructs `Principal` from JWT claims. Later phases' `search_knowledge` tool calls `retrieval_filter`.

- [ ] **Step 1: Write the failing test**

Create `backend/app/rbac/__init__.py` (empty), then `backend/tests/test_rbac_matrix.py`:

```python
import pytest

from app.rbac.policy import (
    Principal, RetrievalDenied, map_access_classification,
    map_escalation_authority, retrieval_filter,
)


def _principal(role, **overrides):
    base = dict(
        kind="user", user_id="u1", role=role, clearance=None,
        department=None, employee_ref=None, helpdesk_ref=None,
    )
    base.update(overrides)
    return Principal(**base)


# -- clearance / escalation mapping ------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Standard", "standard"),
    ("Sensitive business-data access", "sensitive"),
    ("Privileged production access with approval", "privileged"),
])
def test_map_access_classification_known_values(raw, expected):
    assert map_access_classification(raw).value == expected


def test_map_access_classification_unknown_value_raises():
    with pytest.raises(ValueError):
        map_access_classification("Something else entirely")


@pytest.mark.parametrize("raw,expected", [("Standard", "standard"), ("High", "high")])
def test_map_escalation_authority_known_values(raw, expected):
    assert map_escalation_authority(raw) == expected


def test_map_escalation_authority_unknown_value_raises():
    with pytest.raises(ValueError):
        map_escalation_authority("Extreme")


# -- guest: denied on every people/lesson collection -------------------------

@pytest.mark.parametrize("collection", ["employees", "helpdesk", "lessons"])
def test_guest_denied_on_every_collection(collection):
    guest = _principal("guest", kind="guest", user_id=None)
    with pytest.raises(RetrievalDenied):
        retrieval_filter(guest, collection)


# -- employee / standard ------------------------------------------------------

def test_standard_employee_sees_only_own_employee_record():
    principal = _principal("employee", clearance="standard", employee_ref="EMP-042")
    assert retrieval_filter(principal, "employees") == {"employee_id": "EMP-042"}


def test_standard_employee_sees_only_routing_section_of_helpdesk():
    principal = _principal("employee", clearance="standard")
    result = retrieval_filter(principal, "helpdesk")
    assert result == {"section": {"$in": ["Routing guidance"]}}


# -- employee / sensitive -----------------------------------------------------

def test_sensitive_employee_sees_own_record_or_department():
    principal = _principal(
        "employee", clearance="sensitive", employee_ref="EMP-042", department="Finance",
    )
    result = retrieval_filter(principal, "employees")
    assert result == {"$or": [
        {"employee_id": "EMP-042"},
        {"department": "Finance"},
    ]}


def test_sensitive_employee_helpdesk_scope_same_as_standard():
    principal = _principal("employee", clearance="sensitive")
    assert retrieval_filter(principal, "helpdesk") == {"section": {"$in": ["Routing guidance"]}}


# -- employee / privileged ----------------------------------------------------

def test_privileged_employee_sees_own_dept_and_other_depts_excluding_hr_legal():
    principal = _principal(
        "employee", clearance="privileged", employee_ref="EMP-001", department="Engineering",
    )
    result = retrieval_filter(principal, "employees")
    assert result == {"$or": [
        {"employee_id": "EMP-001"},
        {"department": "Engineering"},
        {"department": {"$nin": ["HR", "Legal"]}},
    ]}


def test_privileged_employee_sees_full_helpdesk_documents():
    principal = _principal("employee", clearance="privileged")
    assert retrieval_filter(principal, "helpdesk") == {}


# -- helpdesk ------------------------------------------------------------------

def test_helpdesk_sees_only_requesters_on_their_tickets():
    principal = _principal("helpdesk", helpdesk_ref="HD-001")
    result = retrieval_filter(
        principal, "employees", helpdesk_visible_employee_ids=["EMP-007", "EMP-018"]
    )
    assert result == {"employee_id": {"$in": ["EMP-007", "EMP-018"]}}


def test_helpdesk_with_no_assigned_tickets_sees_no_employees():
    principal = _principal("helpdesk", helpdesk_ref="HD-001")
    result = retrieval_filter(principal, "employees")
    assert result == {"employee_id": {"$in": []}}


def test_helpdesk_sees_full_helpdesk_documents():
    principal = _principal("helpdesk", helpdesk_ref="HD-001")
    assert retrieval_filter(principal, "helpdesk") == {}


# -- admin ----------------------------------------------------------------------

@pytest.mark.parametrize("collection", ["employees", "helpdesk"])
def test_admin_is_unrestricted(collection):
    principal = _principal("admin")
    assert retrieval_filter(principal, collection) == {}


# -- lessons: allowed for everyone except guest ---------------------------------

@pytest.mark.parametrize("role,extra", [
    ("employee", {"clearance": "standard"}),
    ("helpdesk", {}),
    ("admin", {}),
])
def test_lessons_allowed_for_every_non_guest_role(role, extra):
    principal = _principal(role, **extra)
    assert retrieval_filter(principal, "lessons") == {}


# -- unknown inputs ---------------------------------------------------------------

def test_unknown_collection_raises_value_error():
    principal = _principal("admin")
    with pytest.raises(ValueError):
        retrieval_filter(principal, "not_a_real_collection")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_rbac_matrix.py -v`
Expected: FAIL/ERROR — `app.rbac.policy` does not exist yet.

- [ ] **Step 3: Write `backend/app/rbac/policy.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Clearance

# The "helpdesk" collection is chunked by Markdown `##` heading at ingest
# time (Phase 2 plan). "Routing guidance" is the heading name in every
# helpdesk profile that carries routing-relevant information without
# exposing the full support playbook. This constant is verified against
# real ingested `section` metadata by the Phase 2 retrieval-eval gate.
RESTRICTED_HELPDESK_SECTIONS = {"Routing guidance"}

ACCESS_CLASSIFICATION_MAP = {
    "Standard": Clearance.STANDARD,
    "Sensitive business-data access": Clearance.SENSITIVE,
    "Privileged production access with approval": Clearance.PRIVILEGED,
}


class RetrievalDenied(PermissionError):
    """Raised when a principal has no retrieval access to a collection at all."""


@dataclass(frozen=True)
class Principal:
    kind: str  # "user" | "guest"
    user_id: str | None
    role: str  # "guest" | "employee" | "helpdesk" | "admin"
    clearance: str | None
    department: str | None
    employee_ref: str | None
    helpdesk_ref: str | None


def map_access_classification(raw: str) -> Clearance:
    raw = raw.strip()
    if raw not in ACCESS_CLASSIFICATION_MAP:
        raise ValueError(f"Unknown access classification: {raw!r}")
    return ACCESS_CLASSIFICATION_MAP[raw]


def map_escalation_authority(raw: str) -> str:
    normalized = raw.strip().lower()
    if normalized not in {"standard", "high"}:
        raise ValueError(f"Unknown escalation authority: {raw!r}")
    return normalized


def retrieval_filter(
    principal: Principal,
    collection: str,
    *,
    helpdesk_visible_employee_ids: list[str] | None = None,
) -> dict:
    """
    Returns a Chroma `where` clause scoped to what `principal` may see in
    `collection`. Raises RetrievalDenied if the principal has no access to
    this collection at all. The filter returned here is computed
    server-side and must be merged with AND into every retrieval call — a
    model-supplied filter is never accepted (spec section 6.2).
    """
    if collection not in ("employees", "helpdesk", "lessons"):
        raise ValueError(f"unknown collection: {collection!r}")

    if principal.role == "guest":
        raise RetrievalDenied(f"guests cannot search {collection!r}")

    if collection == "lessons":
        return {}

    if principal.role == "admin":
        return {}

    if collection == "helpdesk":
        if principal.role == "helpdesk":
            return {}
        if principal.role == "employee" and principal.clearance == Clearance.PRIVILEGED.value:
            return {}
        return {"section": {"$in": list(RESTRICTED_HELPDESK_SECTIONS)}}

    # collection == "employees"
    if principal.role == "helpdesk":
        ids = helpdesk_visible_employee_ids or []
        return {"employee_id": {"$in": ids}}

    if principal.role == "employee":
        if principal.clearance == Clearance.STANDARD.value:
            return {"employee_id": principal.employee_ref}
        if principal.clearance == Clearance.SENSITIVE.value:
            return {"$or": [
                {"employee_id": principal.employee_ref},
                {"department": principal.department},
            ]}
        if principal.clearance == Clearance.PRIVILEGED.value:
            return {"$or": [
                {"employee_id": principal.employee_ref},
                {"department": principal.department},
                {"department": {"$nin": ["HR", "Legal"]}},
            ]}
        raise RetrievalDenied(f"unrecognized clearance: {principal.clearance!r}")

    raise RetrievalDenied(f"unrecognized role: {principal.role!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_rbac_matrix.py -v`
Expected: PASS (18 tests — every cell of the spec §6.2 matrix plus the mapping functions).

- [ ] **Step 5: Commit**

```bash
git add backend/app/rbac/__init__.py backend/app/rbac/policy.py backend/tests/test_rbac_matrix.py
git commit -m "Add RBAC retrieval-filter policy covering the full clearance matrix"
```

---

### Task 6: Seed script — 126 accounts from `corporate_rag_dataset/`

**Files:**
- Create: `backend/app/db/seed.py`
- Test: `backend/tests/test_seed.py`

**Interfaces:**
- Consumes: `hash_password` (Task 4), `map_access_classification`/`map_escalation_authority` (Task 5), `User`/`Role`/`Clearance`/`EscalationAuthority` (Task 3), `get_settings()` (Task 2), `db_session` fixture (Task 3).
- Produces: `seed(session=None) -> dict[str, int]` (returns `{"admin": 1, "employee": 100, "helpdesk": 25}`), idempotent. Callable as `python -m app.db.seed` via the Makefile `seed` target. Task 7's integration tests call `seed(session=db_session)` to populate test data.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_seed.py`:

```python
from app.db.models import Clearance, EscalationAuthority, Role, User
from app.db.seed import seed


def test_seed_creates_126_accounts(db_session):
    counts = seed(session=db_session)
    assert counts == {"admin": 1, "employee": 100, "helpdesk": 25}
    total = db_session.query(User).count()
    assert total == 126


def test_seed_maps_emp001_to_privileged_clearance(db_session):
    seed(session=db_session)
    emp001 = db_session.query(User).filter_by(employee_ref="EMP-001").one()
    assert emp001.clearance == Clearance.PRIVILEGED
    assert emp001.department == "Engineering"
    assert emp001.role == Role.EMPLOYEE
    assert emp001.full_name == "Narek Keller"


def test_seed_maps_hd001_specialization_and_escalation(db_session):
    seed(session=db_session)
    hd001 = db_session.query(User).filter_by(helpdesk_ref="HD-001").one()
    assert hd001.specialization == "Identity and Access Management"
    assert hd001.escalation_authority == EscalationAuthority.STANDARD
    assert hd001.role == Role.HELPDESK
    assert hd001.full_name == "Noah Taylor"


def test_seed_admin_account_created(db_session):
    seed(session=db_session)
    admin = db_session.query(User).filter_by(username="admin").one()
    assert admin.role == Role.ADMIN


def test_seed_is_idempotent(db_session):
    first = seed(session=db_session)
    second = seed(session=db_session)
    assert first == second
    total = db_session.query(User).count()
    assert total == 126
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_seed.py -v`
Expected: FAIL/ERROR — `app.db.seed` does not exist yet.

- [ ] **Step 3: Write `backend/app/db/seed.py`**

```python
from __future__ import annotations

import re
import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.db.models import EscalationAuthority, Role, User
from app.db.session import get_sessionmaker
from app.rbac.policy import map_access_classification, map_escalation_authority

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATASET_DIR = REPO_ROOT / "corporate_rag_dataset"

FIELD_RE = re.compile(r"^\*\*([^*:]+):\*\*\s*(.+?)\s*$", re.MULTILINE)
ACCESS_CLASS_RE = re.compile(r"Access classification:\s*([^.\n]+)")
NAME_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _parse_fields(text: str) -> dict[str, str]:
    return {key.strip(): value.strip() for key, value in FIELD_RE.findall(text)}


def _parse_employee_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    fields = _parse_fields(text)
    name_match = NAME_RE.search(text)
    access_match = ACCESS_CLASS_RE.search(text)
    if not access_match:
        raise ValueError(f"{path.name}: no 'Access classification' line found")
    employee_id = fields["Employee ID"]
    email = fields["Corporate email"]
    return {
        "username": email.split("@")[0],
        "email": email,
        "full_name": name_match.group(1).strip() if name_match else employee_id,
        "role": Role.EMPLOYEE,
        "clearance": map_access_classification(access_match.group(1)),
        "department": fields.get("Department"),
        "employee_ref": employee_id,
        "location": fields.get("Location"),
    }


def _parse_helpdesk_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    fields = _parse_fields(text)
    name_match = NAME_RE.search(text)
    helpdesk_id = fields["Helpdesk ID"]
    display_name = name_match.group(1).strip() if name_match else helpdesk_id
    username = display_name.lower().replace(" ", ".")
    return {
        "username": username,
        "email": f"{username}@northstar.example",
        "full_name": display_name,
        "role": Role.HELPDESK,
        "helpdesk_ref": helpdesk_id,
        "specialization": fields.get("Primary specialization"),
        "escalation_authority": EscalationAuthority(
            map_escalation_authority(fields["Escalation authority"])
        ),
        "shift": fields.get("Shift"),
    }


def _upsert_user(session, *, password_hash: str, **fields) -> None:
    stmt = pg_insert(User).values(password_hash=password_hash, is_active=True, **fields)
    update_cols = {k: stmt.excluded[k] for k in fields if k != "username"}
    update_cols["password_hash"] = stmt.excluded.password_hash
    stmt = stmt.on_conflict_do_update(index_elements=["username"], set_=update_cols)
    session.execute(stmt)


def seed(session=None) -> dict[str, int]:
    from app.auth.security import hash_password

    settings = get_settings()
    owns_session = session is None
    if owns_session:
        session = get_sessionmaker()()

    counts = {"admin": 0, "employee": 0, "helpdesk": 0}
    try:
        _upsert_user(
            session,
            username=settings.admin_username,
            email=f"{settings.admin_username}@northstar.example",
            full_name="Administrator",
            role=Role.ADMIN,
            password_hash=hash_password(settings.admin_password),
        )
        counts["admin"] = 1

        seed_hash = hash_password(settings.seed_user_password)

        for path in sorted((DATASET_DIR / "employees").glob("EMP-*.md")):
            fields = _parse_employee_file(path)
            _upsert_user(session, password_hash=seed_hash, **fields)
            counts["employee"] += 1

        for path in sorted((DATASET_DIR / "helpdesk").glob("HD-*.md")):
            fields = _parse_helpdesk_file(path)
            _upsert_user(session, password_hash=seed_hash, **fields)
            counts["helpdesk"] += 1

        session.commit()
    finally:
        if owns_session:
            session.close()

    return counts


if __name__ == "__main__":
    result = seed()
    print(f"Seeded: {result}", file=sys.stderr)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_seed.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/seed.py backend/tests/test_seed.py
git commit -m "Add idempotent seed script for 126 accounts from the RAG dataset"
```

---

### Task 7: Auth router + FastAPI app — login, guest, refresh, logout, me

**Files:**
- Create: `backend/app/deps.py`
- Create: `backend/app/auth/router.py`
- Create: `backend/app/main.py`
- Modify: `backend/tests/conftest.py` (append the `client` fixture)
- Test: `backend/tests/test_auth_router.py`

**Interfaces:**
- Consumes: `Principal` + `RetrievalDenied` (Task 5), `hash_password`/`verify_password`/`create_access_token`/`create_refresh_token`/`decode_token`/`hash_refresh_token` (Task 4), `User`/`RefreshToken` (Task 3), `get_db` (Task 3), `get_settings()` (Task 2), `seed()` (Task 6).
- Produces: `app` (the FastAPI instance, `backend/app/main.py`), `get_current_principal`/`CurrentPrincipal`/`DbSession`/`require_role` (`backend/app/deps.py`) — every later phase's routers depend on `CurrentPrincipal` and `DbSession`. Live endpoints: `POST /api/auth/login`, `POST /api/auth/guest`, `POST /api/auth/refresh`, `POST /api/auth/logout`, `GET /api/auth/me`, `GET /api/health`.

- [ ] **Step 1: Write `backend/app/deps.py`**

```python
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.security import decode_token
from app.db.session import get_db
from app.rbac.policy import Principal

DbSession = Annotated[Session, Depends(get_db)]


def get_current_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = decode_token(token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if claims.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")
    return Principal(
        kind=claims["kind"],
        user_id=claims.get("user_id"),
        role=claims["role"],
        clearance=claims.get("clearance"),
        department=claims.get("department"),
        employee_ref=claims.get("employee_ref"),
        helpdesk_ref=claims.get("helpdesk_ref"),
    )


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_role(*allowed: str):
    def _check(principal: CurrentPrincipal) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return principal
    return _check
```

- [ ] **Step 2: Write `backend/app/auth/router.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response, status
from pydantic import BaseModel, EmailStr

from app.auth.security import (
    create_access_token, create_refresh_token, decode_token,
    hash_refresh_token, verify_password,
)
from app.db.models import RefreshToken, User
from app.deps import CurrentPrincipal, DbSession

router = APIRouter(prefix="/api/auth", tags=["auth"])

GENERIC_AUTH_ERROR = "Invalid username or password"
REFRESH_COOKIE_NAME = "refresh_token"
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)]


class LoginRequest(BaseModel):
    username: str
    password: str


class GuestRequest(BaseModel):
    name: str
    email: EmailStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PrincipalResponse(BaseModel):
    kind: str
    user_id: str | None
    role: str
    clearance: str | None
    department: str | None
    employee_ref: str | None
    helpdesk_ref: str | None


def _claims_for_user(user: User) -> dict:
    return {
        "sub": str(user.id),
        "kind": "user",
        "user_id": str(user.id),
        "role": user.role.value,
        "clearance": user.clearance.value if user.clearance else None,
        "department": user.department,
        "employee_ref": user.employee_ref,
        "helpdesk_ref": user.helpdesk_ref,
    }


def _issue_tokens(db: DbSession, response: Response, claims: dict, subject: str) -> TokenResponse:
    access_token = create_access_token(claims)
    if claims["kind"] == "user":
        raw_refresh, token_hash, expires_at = create_refresh_token(subject=subject)
        db.add(RefreshToken(user_id=uuid.UUID(subject), token_hash=token_hash, expires_at=expires_at))
        db.commit()
        response.set_cookie(
            REFRESH_COOKIE_NAME, raw_refresh, httponly=True, samesite="strict",
            max_age=7 * 24 * 3600, path="/api/auth",
        )
    return TokenResponse(access_token=access_token)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, response: Response, db: DbSession) -> TokenResponse:
    user = db.query(User).filter_by(username=payload.username).one_or_none()
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_AUTH_ERROR)
    return _issue_tokens(db, response, _claims_for_user(user), subject=str(user.id))


@router.post("/guest", response_model=TokenResponse)
def guest_login(payload: GuestRequest, response: Response, db: DbSession) -> TokenResponse:
    claims = {
        "sub": payload.email,
        "kind": "guest",
        "user_id": None,
        "role": "guest",
        "clearance": None,
        "department": None,
        "employee_ref": None,
        "helpdesk_ref": None,
        "guest_name": payload.name,
        "guest_email": payload.email,
    }
    return _issue_tokens(db, response, claims, subject=payload.email)


@router.post("/refresh", response_model=TokenResponse)
def refresh(response: Response, db: DbSession, refresh_token: RefreshCookie = None) -> TokenResponse:
    if refresh_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")
    try:
        claims = decode_token(refresh_token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if claims.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")
    token_hash = hash_refresh_token(refresh_token)
    stored = db.query(RefreshToken).filter_by(token_hash=token_hash).one_or_none()
    if stored is None or stored.revoked_at is not None or stored.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token no longer valid")
    user = db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token no longer valid")
    stored.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return _issue_tokens(db, response, _claims_for_user(user), subject=str(user.id))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, db: DbSession, refresh_token: RefreshCookie = None) -> None:
    if refresh_token:
        token_hash = hash_refresh_token(refresh_token)
        stored = db.query(RefreshToken).filter_by(token_hash=token_hash).one_or_none()
        if stored is not None:
            stored.revoked_at = datetime.now(timezone.utc)
            db.commit()
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/auth")


@router.get("/me", response_model=PrincipalResponse)
def me(principal: CurrentPrincipal) -> PrincipalResponse:
    return PrincipalResponse(
        kind=principal.kind, user_id=principal.user_id, role=principal.role,
        clearance=principal.clearance, department=principal.department,
        employee_ref=principal.employee_ref, helpdesk_ref=principal.helpdesk_ref,
    )
```

- [ ] **Step 3: Write `backend/app/main.py`**

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.config import get_settings

settings = get_settings()

app = FastAPI(title="Agentic Helpdesk")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 4: Append the `client` fixture to `backend/tests/conftest.py`**

Add to the end of `backend/tests/conftest.py`:

```python
from fastapi.testclient import TestClient

from app.db.session import get_db as _get_db


@pytest.fixture()
def client(db_session):
    from app.main import app

    def _override_get_db():
        yield db_session

    app.dependency_overrides[_get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
```

- [ ] **Step 5: Write the failing test**

Create `backend/tests/test_auth_router.py`:

```python
from app.config import get_settings
from app.db.seed import seed


def test_admin_login_succeeds(client, db_session):
    seed(session=db_session)
    settings = get_settings()
    response = client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in response.cookies


def test_login_wrong_password_returns_generic_error(client, db_session):
    seed(session=db_session)
    settings = get_settings()
    response = client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": "not-the-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_login_nonexistent_user_returns_same_generic_error(client, db_session):
    response = client.post(
        "/api/auth/login", json={"username": "nobody", "password": "whatever"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_guest_login_returns_token_without_user_row(client):
    response = client.post(
        "/api/auth/guest", json={"name": "Curious Visitor", "email": "visitor@example.com"},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert "refresh_token" not in response.cookies


def test_me_returns_principal_matching_token(client, db_session):
    seed(session=db_session)
    settings = get_settings()
    login_response = client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    access_token = login_response.json()["access_token"]
    response = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "admin"
    assert body["kind"] == "user"


def test_refresh_issues_new_access_token(client, db_session):
    seed(session=db_session)
    settings = get_settings()
    login_response = client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    old_access_token = login_response.json()["access_token"]
    refresh_response = client.post("/api/auth/refresh")
    assert refresh_response.status_code == 200
    new_access_token = refresh_response.json()["access_token"]
    assert new_access_token != old_access_token


def test_logout_revokes_refresh_token(client, db_session):
    seed(session=db_session)
    settings = get_settings()
    client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 204
    refresh_response = client.post("/api/auth/refresh")
    assert refresh_response.status_code == 401


def test_me_without_token_is_unauthorized(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_auth_router.py -v`
Expected: FAIL/ERROR — `app.deps`, `app.auth.router`, `app.main` don't exist yet.

- [ ] **Step 7: Run the full suite to verify everything passes**

Run: `cd backend && uv run pytest -v`
Expected: PASS — all tests across Tasks 2–7 (config, schema, security, RBAC, seed, auth router). This is the Foundation plan's final gate: `pytest` green, and it exercises spec success criterion #1 (admin login) end-to-end.

- [ ] **Step 8: Manual smoke test**

Run: `make migrate && make seed && make dev` (in one terminal), then in another:

```bash
curl -s http://127.0.0.1:8080/api/health
curl -s -X POST http://127.0.0.1:8080/api/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin"}'
```

Expected: first call returns `{"status":"ok"}`; second returns a JSON body containing `access_token`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/deps.py backend/app/auth/router.py backend/app/main.py backend/tests/conftest.py backend/tests/test_auth_router.py
git commit -m "Add auth router and FastAPI app: login, guest, refresh, logout, me"
```

---

## Self-Review

**Spec coverage.** Every Foundation-scoped requirement from the design spec has a task: environment/db setup → Task 1; `.env` boot validation and the `admin`/`admin` warning → Task 2; the full §5 data model (with the two documented, non-behavior-changing gap-fills: `users.full_name` and concrete `notifications` columns) → Task 3; bcrypt + JWT per §12.5 → Task 4; the §6.2 RBAC matrix in full → Task 5; the §5.6 seed spec (126 idempotent accounts, clearance/escalation mapping) → Task 6; the §14 auth endpoints and generic-failure-message rule → Task 7. Spec success criterion #1 (admin and seeded logins reach the app) is exercised by Task 7's tests. Retrieval-scoping (success criterion #2) has its policy layer fully built and matrix-tested here; the tool-layer proof (calling the tool directly, bypassing the prompt) lands in the Phase 4 (agent/tools) plan, since no tool exists yet — noted as this plan's boundary, not a gap.

**Placeholder scan.** No "TBD"/"TODO"/vague instructions. The one deferred item (`_helpdesk_visible_employee_ids` SQL lookup) was designed out entirely by making `retrieval_filter` accept the ids as a parameter rather than fetching them itself — `rbac/policy.py` has zero I/O and zero forward references to unbuilt modules.

**Type consistency.** `Principal` fields (`kind`, `user_id`, `role`, `clearance`, `department`, `employee_ref`, `helpdesk_ref`) are identical across Task 5's dataclass, Task 7's JWT claim construction (`_claims_for_user`, guest claims), and Task 7's `get_current_principal` reconstruction — checked field-by-field. `seed()`'s return shape `{"admin": int, "employee": int, "helpdesk": int}` matches every test's assertion. `retrieval_filter`'s keyword-only `helpdesk_visible_employee_ids` matches its two call sites in `test_rbac_matrix.py`.

---

**Next plan:** RAG ingestion + retrieval (spec §7) — chunking, the `mcp`/`direct` Chroma backends, and the evaluation-gate script against `corporate_rag_dataset/evaluation/`. Written once this plan's tasks are executed and verified.
