"""Cross-platform task runner — a `make`-free equivalent of the Makefile.

`make` is not installed on every dev machine (this one included). Every task
here does exactly what the corresponding Makefile target does, using only
the stdlib and the packages already in this project — no new dependency.

Run from the `backend/` directory:
    uv run python tasks.py <task>

Tasks: db-up, db-create, migrate, seed, dev, test

The Makefile targets delegate to this script, so there is one implementation
either way you invoke it.
"""

from __future__ import annotations

import subprocess
import sys
import urllib.error
import urllib.request


def db_up() -> int:
    print("Checking Postgres...")
    if subprocess.run(["docker", "exec", "postgres18", "pg_isready", "-U", "postgres"]).returncode != 0:
        print("Postgres not reachable")
        return 1
    print("Checking Chroma...")
    try:
        urllib.request.urlopen("http://localhost:8000/api/v2/heartbeat", timeout=5)
    except (urllib.error.URLError, OSError) as exc:
        print(f"Chroma not reachable: {exc}")
        return 1
    print("Both services healthy.")
    return 0


def db_create() -> int:
    """Idempotent: creates the `ticketing` database the app connects to
    (the postgres18 container's default POSTGRES_DB is `mydb`, not
    `ticketing`). Safe to run repeatedly, including against an
    already-set-up environment."""
    check = subprocess.run(
        [
            "docker", "exec", "postgres18", "psql", "-U", "postgres", "-tc",
            "SELECT 1 FROM pg_database WHERE datname = 'ticketing'",
        ],
        capture_output=True,
        text=True,
    )
    if "1" in check.stdout:
        print("Database 'ticketing' already exists.")
        return 0
    return subprocess.run(
        ["docker", "exec", "postgres18", "psql", "-U", "postgres", "-c", "CREATE DATABASE ticketing;"]
    ).returncode


def migrate() -> int:
    return subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"]).returncode


def seed() -> int:
    return subprocess.run([sys.executable, "-m", "app.db.seed"]).returncode


def dev() -> int:
    from app.config import get_settings

    settings = get_settings()
    return subprocess.run(
        [
            sys.executable, "-m", "uvicorn", "app.main:app", "--reload",
            "--host", settings.backend_host, "--port", str(settings.backend_port),
        ]
    ).returncode


def test() -> int:
    return subprocess.run([sys.executable, "-m", "pytest", "-v"]).returncode


TASKS = {
    "db-up": db_up,
    "db-create": db_create,
    "migrate": migrate,
    "seed": seed,
    "dev": dev,
    "test": test,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in TASKS:
        print(f"Usage: uv run python tasks.py <{'|'.join(TASKS)}>")
        return 1
    return TASKS[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
