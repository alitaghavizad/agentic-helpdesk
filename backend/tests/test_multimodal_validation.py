"""Validation is the whole security boundary for uploads: everything past it
is treated as a file we chose to keep. These tests are deliberately adversarial
-- the interesting cases are the mismatches, not the happy path."""
from __future__ import annotations

import pytest

from app.db.models import AttachmentKind
from app.multimodal import validation


# ---- fixtures: real magic bytes, built literally -------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
PDF = b"%PDF-1.4\n" + b"\x00" * 32
WAV = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 32
MP3_ID3 = b"ID3\x03\x00" + b"\x00" * 32
MP3_SYNC = b"\xff\xfb\x90\x00" + b"\x00" * 32
M4A = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 32
OGG = b"OggS\x00\x02" + b"\x00" * 32


@pytest.mark.parametrize("data,expected", [
    (PNG, "png"), (JPEG, "jpg"), (WEBP, "webp"), (PDF, "pdf"),
    (WAV, "wav"), (MP3_ID3, "mp3"), (MP3_SYNC, "mp3"), (M4A, "m4a"), (OGG, "ogg"),
])
def test_sniff_recognises_every_allowed_type(data, expected):
    assert validation.sniff(data) == expected


def test_sniff_returns_none_for_unrecognised_bytes():
    assert validation.sniff(b"not a real file at all") is None


# ---- the allowlists ------------------------------------------------------

def test_a_disallowed_extension_is_rejected():
    with pytest.raises(validation.RejectedUpload) as exc:
        validation.validate(filename="payload.exe", declared_mime="application/octet-stream", head=PNG)
    assert "extension" in exc.value.reason.lower()


def test_a_disallowed_mime_is_rejected():
    with pytest.raises(validation.RejectedUpload) as exc:
        validation.validate(filename="shot.png", declared_mime="application/x-msdownload", head=PNG)
    assert "mime" in exc.value.reason.lower()


# ---- the mismatch attack, which is the point of sniffing -----------------

def test_a_pdf_wearing_a_png_extension_is_rejected():
    """Both types are individually allowed. The MISMATCH is the signal --
    without this check, an allowlist of extensions is decoration."""
    with pytest.raises(validation.RejectedUpload) as exc:
        validation.validate(filename="innocent.png", declared_mime="image/png", head=PDF)
    assert "match" in exc.value.reason.lower()


def test_unrecognisable_bytes_are_rejected_even_with_a_good_name():
    with pytest.raises(validation.RejectedUpload):
        validation.validate(filename="shot.png", declared_mime="image/png", head=b"garbage")


@pytest.mark.parametrize("filename,mime,head,kind", [
    ("shot.png", "image/png", PNG, AttachmentKind.IMAGE),
    ("scan.pdf", "application/pdf", PDF, AttachmentKind.PDF),
    ("voice.wav", "audio/wav", WAV, AttachmentKind.AUDIO),
    ("voice.mp3", "audio/mpeg", MP3_ID3, AttachmentKind.AUDIO),
])
def test_a_consistent_upload_is_accepted_with_its_kind(filename, mime, head, kind):
    ext, resolved = validation.validate(filename=filename, declared_mime=mime, head=head)
    assert resolved is kind
    assert ext == filename.rsplit(".", 1)[1]


def test_jpeg_accepts_both_spellings_of_its_extension():
    for name in ("photo.jpg", "photo.jpeg"):
        _ext, kind = validation.validate(filename=name, declared_mime="image/jpeg", head=JPEG)
        assert kind is AttachmentKind.IMAGE


# ---- filenames -----------------------------------------------------------

@pytest.mark.parametrize("raw,forbidden", [
    ("../../etc/passwd", "/"),
    (r"..\..\windows\system32", "\\"),
    ("shot\x00.png", "\x00"),
    ("re\nport.pdf", "\n"),
])
def test_sanitize_strips_path_separators_and_control_characters(raw, forbidden):
    assert forbidden not in validation.sanitize_filename(raw)


def test_sanitize_truncates_to_the_column_width():
    assert len(validation.sanitize_filename("a" * 900 + ".png")) <= 500


def test_sanitize_never_returns_empty():
    """An empty filename would render as a blank in the UI and as an empty
    untrusted_data source attribute. Always leave something."""
    assert validation.sanitize_filename("///") != ""
    assert validation.sanitize_filename("") != ""


# ---- storage paths -------------------------------------------------------

def test_storage_relpath_is_content_addressed_and_sharded():
    sha = "a" * 64
    assert validation.storage_relpath(sha) == "aa/" + sha


def test_storage_relpath_rejects_anything_that_is_not_a_hex_digest():
    """The digest is computed by us, never supplied -- but this function
    builds a filesystem path, so it refuses to build one from input that
    could contain a separator."""
    for bad in ("../etc/passwd", "a" * 63, "z" * 64, ""):
        with pytest.raises(ValueError):
            validation.storage_relpath(bad)
