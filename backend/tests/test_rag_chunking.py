from pathlib import Path

import pytest

from app.rag.chunking import OVERVIEW_SECTION, chunk_employee_file, chunk_helpdesk_file

DATASET_DIR = Path(__file__).resolve().parent.parent.parent / "corporate_rag_dataset"


def test_chunk_employee_file_emp001_section_count_and_order():
    chunks = chunk_employee_file(DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md")
    sections = [c.section for c in chunks]
    assert len(chunks) == 13  # Overview + 12 real "##" sections in this file
    assert sections[0] == OVERVIEW_SECTION
    assert sections[1] == "Profile"
    assert "Systems and access" in sections
    assert "RAG evaluation notes" in sections


def test_chunk_employee_file_emp001_metadata_on_every_chunk():
    chunks = chunk_employee_file(DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md")
    for chunk in chunks:
        assert chunk.metadata["employee_id"] == "EMP-001"
        assert chunk.metadata["name"] == "Narek Keller"
        assert chunk.metadata["department"] == "Engineering"
        assert chunk.metadata["role"] == "Engineering Manager"
        assert chunk.metadata["location"] == "London"
        assert chunk.metadata["access_classification"] == "privileged"
        assert chunk.metadata["source_file"] == "EMP-001_Narek_Keller.md"
        assert chunk.metadata["doc_type"] == "employee"
        assert chunk.metadata["section"] == chunk.section


def test_chunk_employee_file_overview_chunk_contains_frontmatter_facts():
    chunks = chunk_employee_file(DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md")
    overview = chunks[0]
    assert overview.section == OVERVIEW_SECTION
    assert "EMP-001" in overview.text
    assert "Engineering Manager" in overview.text


def test_chunk_ids_are_stable_and_unique():
    chunks = chunk_employee_file(DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md")
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))
    assert ids[0] == "EMP-001_Narek_Keller.md::chunk-0"
    assert ids[1] == "EMP-001_Narek_Keller.md::chunk-1"


def test_chunk_employee_file_missing_access_classification_raises(tmp_path):
    bad_file = tmp_path / "EMP-999_Bad_File.md"
    bad_file.write_text(
        "# Bad Person\n\n**Employee ID:** EMP-999\n**Corporate email:** bad@x.example\n\n"
        "## Profile\nNo access classification line here.\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Access classification"):
        chunk_employee_file(bad_file)


def test_chunk_helpdesk_file_hd001_section_count_and_order():
    chunks = chunk_helpdesk_file(DATASET_DIR / "helpdesk" / "HD-001_Noah_Taylor.md")
    sections = [c.section for c in chunks]
    assert len(chunks) == 14  # Overview + 13 real "##" sections in this file
    assert sections[0] == OVERVIEW_SECTION
    assert sections[1] == "Support profile"
    assert "Routing guidance" in sections
    assert "Ticket documentation standards" in sections


def test_chunk_helpdesk_file_hd001_metadata_on_every_chunk():
    chunks = chunk_helpdesk_file(DATASET_DIR / "helpdesk" / "HD-001_Noah_Taylor.md")
    for chunk in chunks:
        assert chunk.metadata["helpdesk_id"] == "HD-001"
        assert chunk.metadata["name"] == "Noah Taylor"
        assert chunk.metadata["role"] == "L1 Support Specialist"
        assert chunk.metadata["specialization"] == "Identity and Access Management"
        assert chunk.metadata["shift"] == "09:00-17:00 CET"
        assert chunk.metadata["escalation_authority"] == "standard"
        assert chunk.metadata["source_file"] == "HD-001_Noah_Taylor.md"
        assert chunk.metadata["doc_type"] == "helpdesk"


def test_chunk_helpdesk_file_overview_chunk_contains_specialization():
    chunks = chunk_helpdesk_file(DATASET_DIR / "helpdesk" / "HD-001_Noah_Taylor.md")
    overview = chunks[0]
    assert overview.section == OVERVIEW_SECTION
    assert "Identity and Access Management" in overview.text
