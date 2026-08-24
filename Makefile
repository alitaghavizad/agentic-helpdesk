.PHONY: db-up migrate seed dev test

db-up:
	@echo "Checking Postgres..."
	@docker exec postgres18 pg_isready -U postgres || (echo "Postgres not reachable" && exit 1)
	@echo "Checking Chroma..."
	@curl -sf http://localhost:8000/api/v2/heartbeat > /dev/null || (echo "Chroma not reachable" && exit 1)
	@echo "Both services healthy."

migrate:
	cd backend && uv run alembic upgrade head

seed:
	cd backend && uv run python -m app.db.seed

dev:
	cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8080

test:
	cd backend && uv run pytest -v
