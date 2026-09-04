from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Integer, Numeric,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: DateTime(timezone=True),
    }


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
    APPROVAL_EXECUTION = "approval_execution"
    # Added in Phase 9 Task 5: admin_patch_lesson/admin_archive_lesson call
    # writer.upsert_embedding, whose real backend (McpChromaBackend) wraps
    # every Chroma call in a tracing span -- span() hard-requires an active
    # Run, and an admin PATCH/DELETE has no ambient one the way a chat turn,
    # dossier build, or reflection does. See app/admin/router.py's _reembed.
    LESSON_EDIT = "lesson_edit"


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
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="role", values_callable=lambda x: [e.value for e in x]), nullable=False)
    clearance: Mapped[Clearance | None] = mapped_column(SAEnum(Clearance, name="clearance", values_callable=lambda x: [e.value for e in x]), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employee_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    helpdesk_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    specialization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    escalation_authority: Mapped[EscalationAuthority | None] = mapped_column(
        SAEnum(EscalationAuthority, name="escalation_authority", values_callable=lambda x: [e.value for e in x]), nullable=True
    )
    shift: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


# ---- Conversation ---------------------------------------------------------------

class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL) <> (guest_name IS NOT NULL AND guest_email IS NOT NULL)",
            name="ck_conversations_requester_present",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    guest_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guest_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[ConversationStatus] = mapped_column(
        SAEnum(ConversationStatus, name="conversation_status", values_callable=lambda x: [e.value for e in x]), nullable=False,
        default=ConversationStatus.ACTIVE,
    )
    # Indexed: the admin conversation list's ORDER BY. See migration
    # f9824ef578ed.
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False, index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    role: Mapped[MessageRole] = mapped_column(SAEnum(MessageRole, name="message_role", values_callable=lambda x: [e.value for e in x]), nullable=False)
    content: Mapped[list | dict] = mapped_column(JSONB, nullable=False)
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
    kind: Mapped[AttachmentKind] = mapped_column(SAEnum(AttachmentKind, name="attachment_kind", values_callable=lambda x: [e.value for e in x]), nullable=False)
    parse_status: Mapped[ParseStatus] = mapped_column(
        SAEnum(ParseStatus, name="parse_status", values_callable=lambda x: [e.value for e in x]), nullable=False, default=ParseStatus.PENDING
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
    category: Mapped[TaskCategory] = mapped_column(SAEnum(TaskCategory, name="task_category", values_callable=lambda x: [e.value for e in x]), nullable=False)
    severity: Mapped[Severity] = mapped_column(SAEnum(Severity, name="severity", values_callable=lambda x: [e.value for e in x]), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    affected_systems: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    classified_by_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    resolution_path: Mapped[ResolutionPath] = mapped_column(
        SAEnum(ResolutionPath, name="resolution_path", values_callable=lambda x: [e.value for e in x]), nullable=False, default=ResolutionPath.PENDING
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
    priority: Mapped[TicketPriority] = mapped_column(SAEnum(TicketPriority, name="ticket_priority", values_callable=lambda x: [e.value for e in x]), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, name="ticket_status", values_callable=lambda x: [e.value for e in x]), nullable=False, default=TicketStatus.OPEN
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
        SAEnum(ApprovalActionType, name="approval_action_type", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    action_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(SAEnum(RiskLevel, name="risk_level", values_callable=lambda x: [e.value for e in x]), nullable=False)
    agent_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus, name="approval_status", values_callable=lambda x: [e.value for e in x]), nullable=False, default=ApprovalStatus.PENDING
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
    # Spec 5.3's invariant lives in the database, not in application code:
    # this column mirrors the approval's status through a composite FK with
    # ON UPDATE CASCADE, and a CHECK forbids the pre-approval states. Never
    # set it by hand to something the approval is not actually in -- the FK
    # will reject the row. See the phase 6 spec section 7.
    approval_status_at_send: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus, name="approval_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[EmailStatus] = mapped_column(
        SAEnum(EmailStatus, name="email_status", values_callable=lambda x: [e.value for e in x]), nullable=False, default=EmailStatus.QUEUED
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
    trigger: Mapped[RunTrigger] = mapped_column(SAEnum(RunTrigger, name="run_trigger", values_callable=lambda x: [e.value for e in x]), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status", values_callable=lambda x: [e.value for e in x]), nullable=False, default=RunStatus.RUNNING
    )
    # Indexed: the admin run list's ORDER BY, and every overview counter is a
    # `started_at >= start-of-today` scan. See migration f9824ef578ed.
    started_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False, index=True,
    )
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
    parent_span_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("spans.id"), nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[SpanKind] = mapped_column(SAEnum(SpanKind, name="span_kind", values_callable=lambda x: [e.value for e in x]), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SpanStatus] = mapped_column(
        SAEnum(SpanStatus, name="span_status", values_callable=lambda x: [e.value for e in x]), nullable=False, default=SpanStatus.OK
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
    actor_type: Mapped[ActorType] = mapped_column(SAEnum(ActorType, name="actor_type", values_callable=lambda x: [e.value for e in x]), nullable=False)
    # Indexed: every one of these is a filter column on the admin audit list,
    # over a table spec 5.4 makes append-only (it only ever grows). See
    # migration f9824ef578ed.
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Indexed: the audit list's ORDER BY and its date-range filter.
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False, index=True,
    )


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
        SAEnum(LessonConfidence, name="lesson_confidence", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    embedded_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[LessonStatus] = mapped_column(
        SAEnum(LessonStatus, name="lesson_status", values_callable=lambda x: [e.value for e in x]), nullable=False, default=LessonStatus.ACTIVE
    )
    created_by_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


# ---- Notifications ----------------------------------------------------------------

class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    type: Mapped[NotificationType] = mapped_column(
        SAEnum(NotificationType, name="notification_type", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
