.PHONY: db-up db-create migrate seed dev test

db-up:
	@echo "Checking Postgres..."
	@docker exec postgres18 pg_isready -U postgres || (echo "Postgres not reachable" && exit 1)
	@echo "Checking Chroma..."
	@curl -sf http://localhost:8000/api/v2/heartbeat > /dev/null || (echo "Chroma not reachable" && exit 1)
	@echo "Both services healthy."

# Idempotent: creates the `ticketing` database the app actually connects to
# (the postgres18 container's default POSTGRES_DB is `mydb`, not `ticketing`).
# Safe to run repeatedly, including against an already-set-up environment.
db-create:
	docker exec postgres18 psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname = 'ticketing'" | grep -q 1 || docker exec postgres18 psql -U postgres -c "CREATE DATABASE ticketing;"

migrate:
	cd backend && uv run alembic upgrade head

seed:
	cd backend && uv run python -m app.db.seed

dev:
	cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8080

test:
	cd backend && uv run pytest -v
