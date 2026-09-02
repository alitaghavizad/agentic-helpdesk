from __future__ import annotations

import pytest

from app.main import app

# Endpoints whose published 200 body must be a real named schema. Each of
# these already returns a fixed, known shape; publishing `object` meant a
# generated client could not tell a dossier from an empty dict.
TYPED = [
    ("post", "/api/admin/tickets/{ticket_id}/dossier", "IncidentDossier"),
    ("patch", "/api/admin/users/{user_id}", "UserPatchResult"),
    ("patch", "/api/admin/lessons/{lesson_id}", "LessonSummary"),
    ("delete", "/api/admin/lessons/{lesson_id}", "LessonDeleteResult"),
    ("get", "/api/admin/conversations/{conversation_id}", "ConversationDetail"),
    ("get", "/api/conversations/{conversation_id}", "ConversationResponse"),
]


@pytest.fixture(scope="module")
def schema():
    return app.openapi()


@pytest.mark.parametrize("method,path,expected", TYPED)
def test_endpoint_publishes_a_named_schema(schema, method, path, expected):
    body = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
    assert body.get("$ref") == f"#/components/schemas/{expected}", body


def test_dossier_schema_carries_its_fields(schema):
    """A $ref to an empty model would satisfy the test above while telling a
    client nothing. The dossier is the one whose 15 fields the UI renders."""
    props = schema["components"]["schemas"]["IncidentDossier"]["properties"]
    for field in ("ticket_number", "problem_statement", "requester", "timeline",
                  "knowledge_sources", "risk_flags", "cost_summary"):
        assert field in props


def test_message_view_is_shared_not_duplicated(schema):
    """Both transcript endpoints must reference the SAME MessageView schema.
    Two structurally-identical copies would generate two TypeScript types
    and let the two endpoints drift apart later."""
    conv = schema["components"]["schemas"]["ConversationResponse"]["properties"]["messages"]
    detail = schema["components"]["schemas"]["ConversationDetail"]["properties"]["messages"]
    assert conv["items"]["$ref"] == detail["items"]["$ref"] == "#/components/schemas/MessageView"
