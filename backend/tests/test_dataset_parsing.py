from pathlib import Path

import pytest

from app.dataset.parsing import parse_fields, parse_name, require_field

DATASET_DIR = Path(__file__).resolve().parent.parent.parent / "corporate_rag_dataset"


def test_parse_fields_extracts_bold_field_block():
    text = (DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md").read_text(encoding="utf-8")
    fields = parse_fields(text)
    assert fields["Employee ID"] == "EMP-001"
    assert fields["Department"] == "Engineering"
    assert fields["Corporate email"] == "narek.keller1@northstar.example"


def test_parse_name_extracts_h1_heading():
    text = (DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md").read_text(encoding="utf-8")
    assert parse_name(text, fallback="unknown") == "Narek Keller"


def test_parse_name_falls_back_when_no_heading():
    assert parse_name("no heading here", fallback="EMP-999") == "EMP-999"


def test_require_field_returns_value_when_present():
    fields = {"Employee ID": "EMP-001"}
    assert require_field(fields, "Employee ID", Path("EMP-001.md")) == "EMP-001"


def test_require_field_raises_with_filename_when_missing():
    fields = {}
    with pytest.raises(ValueError, match="EMP-001.md.*Employee ID"):
        require_field(fields, "Employee ID", Path("EMP-001.md"))
