"""The security boundary for uploads (spec 11). Everything downstream treats
a file as one we chose to keep, so every reason to refuse belongs here.

Pure functions over bytes and strings: no database, no network, no
filesystem. That is what makes the adversarial cases cheap to test
exhaustively.

The magic-byte sniffer is hand-rolled rather than delegated to libmagic.
Nine extensions map to eight signatures; a C library that is awkward to
install on Windows is a large dependency for that, and the signatures do
not change.
"""
from __future__ import annotations

import re
import string

from app.db.models import AttachmentKind

MAX_BYTES = 20 * 1024 * 1024

# extension -> (canonical mime, kind). Both spellings of jpeg are listed
# because both are common and both are allowed by spec 11.
ALLOWED: dict[str, tuple[str, AttachmentKind]] = {
    "png": ("image/png", AttachmentKind.IMAGE),
    "jpg": ("image/jpeg", AttachmentKind.IMAGE),
    "jpeg": ("image/jpeg", AttachmentKind.IMAGE),
    "webp": ("image/webp", AttachmentKind.IMAGE),
    "pdf": ("application/pdf", AttachmentKind.PDF),
    "mp3": ("audio/mpeg", AttachmentKind.AUDIO),
    "wav": ("audio/wav", AttachmentKind.AUDIO),
    "m4a": ("audio/mp4", AttachmentKind.AUDIO),
    "ogg": ("audio/ogg", AttachmentKind.AUDIO),
}

# Extra spellings browsers and clients actually send, mapped to the
# extension they are allowed to accompany.
_MIME_ALIASES: dict[str, str] = {
    "image/jpg": "jpg",
    "audio/mp3": "mp3",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/vorbis": "ogg",
    "application/ogg": "ogg",
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FILENAME_SAFE = set(string.ascii_letters + string.digits + " ._-()[]")


class RejectedUpload(ValueError):
    """A refusal with a reason fit to show the uploader. The reason is
    deliberately specific ('extension not allowed', 'content does not match')
    -- a user who mislabels a file should be told which thing was wrong,
    and none of it discloses anything about other users."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def sniff(data: bytes) -> str | None:
    """Returns the extension the BYTES actually are, or None. Order matters:
    the RIFF container prefixes both WEBP and WAV, so those are
    distinguished on the form type at offset 8, not on the prefix."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[4:8] == b"ftyp":
        return "m4a"
    if data.startswith(b"OggS"):
        return "ogg"
    return None


def sanitize_filename(name: str) -> str:
    """Kept for display only -- it never becomes part of a filesystem path
    (storage is content-addressed), so this is defence in depth rather than
    the thing standing between us and traversal. Never returns empty,
    because a blank filename renders as a blank in the UI and as an empty
    `source` attribute on the untrusted_data wrapper."""
    cleaned = "".join(ch for ch in name if ch in _FILENAME_SAFE).strip()
    cleaned = cleaned.lstrip(".")
    return (cleaned[:500]) if cleaned else "attachment"


def _equivalent(declared_mime: str, extension: str) -> bool:
    declared = declared_mime.split(";")[0].strip().lower()
    if ALLOWED[extension][0] == declared:
        return True
    return _MIME_ALIASES.get(declared) == extension


def validate(*, filename: str, declared_mime: str, head: bytes) -> tuple[str, AttachmentKind]:
    """Cheapest and most decisive checks first, so a hostile upload is
    refused before it costs anything.

    The sniff must agree with BOTH the declared MIME and the extension. A
    PDF named `.png` is refused even though both types are individually
    allowed -- the mismatch is the signal, and without this check an
    extension allowlist is decoration."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED:
        raise RejectedUpload(f"extension {extension!r} is not allowed")
    if not _equivalent(declared_mime, extension):
        raise RejectedUpload(f"declared MIME {declared_mime!r} is not allowed for a .{extension} file")

    actual = sniff(head)
    if actual is None:
        raise RejectedUpload("file content was not recognised as any allowed type")
    if ALLOWED[actual][1] is not ALLOWED[extension][1] or (
        actual != extension and ALLOWED[actual][0] != ALLOWED[extension][0]
    ):
        raise RejectedUpload(
            f"file content does not match its name: bytes look like .{actual}, name says .{extension}"
        )
    return extension, ALLOWED[extension][1]


def storage_relpath(sha256: str) -> str:
    """Shards by the first byte so one directory never holds every upload.
    Refuses a non-digest outright: this builds a filesystem path, and the
    digest is the only thing keeping a separator out of it."""
    if not _SHA256_RE.match(sha256):
        raise ValueError(f"not a sha256 hex digest: {sha256!r}")
    return f"{sha256[:2]}/{sha256}"
