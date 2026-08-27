from pathlib import Path

import pytest

from app.rag.chunking import (
    OVERVIEW_SECTION,
    Chunk,
    chunk_employee_file,
    chunk_helpdesk_file,
    drop_nondiscriminating_chunks,
)

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


def test_chunk_employee_file_every_chunk_text_has_identity_prefix():
    chunks = chunk_employee_file(DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md")
    expected_prefix = "Employee: Narek Keller (EMP-001), Engineering, Engineering Manager.\n\n"
    for chunk in chunks:
        assert chunk.text.startswith(expected_prefix)


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


def test_chunk_helpdesk_file_every_chunk_text_has_identity_prefix():
    chunks = chunk_helpdesk_file(DATASET_DIR / "helpdesk" / "HD-001_Noah_Taylor.md")
    expected_prefix = (
        "Helpdesk specialist: Noah Taylor (HD-001), L1 Support Specialist, "
        "specializing in Identity and Access Management.\n\n"
    )
    for chunk in chunks:
        assert chunk.text.startswith(expected_prefix)


def test_drop_nondiscriminating_chunks_removes_sections_identical_across_all_docs():
    """Root cause of the retrieval instability this fixes: sections whose
    prose is byte-identical in every document (helpdesk "Ticket
    documentation standards" and "Security and privacy behavior"; employee
    "Access and authorization boundaries", "Device and endpoint context",
    "Business process behavior") cannot tell two documents apart, but their
    per-document identity prefixes give them just enough variance to spread
    into a dense near-tied band that occupies top-k slots.

    Measured with clean rebuilds of the corpus with and without these
    chunks: Recall@5 0.6958 -> 0.7125, MRR 0.7750 -> 0.8108. The routing
    margin -- how far the correct specialist beats the runner-up -- widened
    from 0.0620 to 0.2523 for the VPN query and 0.0995 to 0.1616 for the
    MFA one. Q025 ("Which helpdesk member should receive a ticket primarily
    about Identity and Access Management?") went from recall@5 = 0.00, with
    every top hit the identical "Security and privacy behavior" chunk from
    an unrelated specialist, to returning HD-001 first.

    Most telling: before the change the *winning* chunk for both the VPN and
    MFA queries was "Ticket documentation standards" -- prose identical
    across all 25 specialists, so it carried no information about which
    specialist won. Ranking was decided by noise.
    """
    chunks = [c for f in sorted((DATASET_DIR / "helpdesk").glob("HD-*.md")) for c in chunk_helpdesk_file(f)]

    kept, dropped_sections = drop_nondiscriminating_chunks(chunks)

    assert "Ticket documentation standards" in dropped_sections
    assert "Security and privacy behavior" in dropped_sections
    # Sections that genuinely differ per specialist must survive -- these
    # are what routing actually depends on.
    assert "Overview" not in dropped_sections
    assert "Routing guidance" not in dropped_sections
    assert "Diagnostic approach" not in dropped_sections

    kept_sections = {c.section for c in kept}
    assert kept_sections.isdisjoint(dropped_sections)
    assert len(kept) < len(chunks)
    # Every surviving section still has exactly one chunk per document.
    assert len(kept) == 25 * len(kept_sections)


def test_drop_nondiscriminating_chunks_keeps_a_corpus_with_no_boilerplate_intact():
    """The rule is "this section cannot discriminate", not "drop a fixed
    list of section names". A single-document corpus has no cross-document
    duplication at all, so nothing may be dropped."""
    chunks = chunk_helpdesk_file(DATASET_DIR / "helpdesk" / "HD-001_Noah_Taylor.md")

    kept, dropped_sections = drop_nondiscriminating_chunks(chunks)

    assert dropped_sections == set()
    assert len(kept) == len(chunks)


def _fake_chunk(source_file: str, section: str, raw_text: str) -> Chunk:
    return Chunk(
        id=f"{source_file}::{section}",
        text=f"Doc: {source_file}\n\n{raw_text}",
        chunk_index=0, source_file=source_file, section=section,
        metadata={"source_file": source_file, "section": section}, raw_text=raw_text,
    )


def test_drop_nondiscriminating_chunks_keeps_a_section_only_some_documents_have():
    """A section present in only SOME documents is highly discriminating --
    its very presence identifies those documents -- even though its prose
    has a single distinct value. Dropping it would delete a real fact.
    An earlier version of this function counted documents corpus-wide
    rather than per-section and got this wrong."""
    chunks = [_fake_chunk(f"doc{i}.md", "Everywhere", "same everywhere") for i in range(5)]
    chunks += [_fake_chunk(f"doc{i}.md", "Overview", f"unique to doc{i}") for i in range(5)]
    # Present in 2 of the 5 documents, identical in both.
    chunks += [_fake_chunk(f"doc{i}.md", "Clearance history", "identical text") for i in range(2)]

    kept, dropped = drop_nondiscriminating_chunks(chunks)

    assert dropped == {"Everywhere"}
    assert "Clearance history" in {c.section for c in kept}


def test_drop_nondiscriminating_chunks_keeps_a_section_with_only_two_distinct_values():
    """The threshold is exact duplication, not "nearly duplicated". A
    section with even two distinct values can tell some documents apart,
    and loosening this was measured to gain nothing (identical Recall@5
    when also dropping sections with up to 8 distinct values)."""
    chunks = [_fake_chunk(f"doc{i}.md", "Escalation rules", "A" if i % 2 else "B") for i in range(6)]
    chunks += [_fake_chunk(f"doc{i}.md", "Boilerplate", "identical") for i in range(6)]

    kept, dropped = drop_nondiscriminating_chunks(chunks)

    assert dropped == {"Boilerplate"}
    assert "Escalation rules" in {c.section for c in kept}
