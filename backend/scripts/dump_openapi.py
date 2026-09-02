"""Writes the live OpenAPI schema to frontend/openapi.json.

The frontend generates its TypeScript types from this file, and both it and
the generated types are committed, so `npm run api:check` can regenerate and
diff them. A generated file nobody regenerates is a file nobody notices
going stale.

Imports the app but starts no server and opens no database connection, so it
runs with Docker stopped and in CI.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.main import app

OUTPUT = Path(__file__).resolve().parent.parent.parent / "frontend" / "openapi.json"


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so the committed file is stable: without it, a schema whose
    # key order shifts between runs shows as a diff with no change in it.
    OUTPUT.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
