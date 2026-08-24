.PHONY: db-up db-create migrate seed dev test

# Every target below just delegates to backend/tasks.py, a cross-platform
# Python task runner. Use that directly if `make` isn't installed:
#   cd backend && uv run python tasks.py <db-up|db-create|migrate|seed|dev|test>

db-up:
	cd backend && uv run python tasks.py db-up

db-create:
	cd backend && uv run python tasks.py db-create

migrate:
	cd backend && uv run python tasks.py migrate

seed:
	cd backend && uv run python tasks.py seed

dev:
	cd backend && uv run python tasks.py dev

test:
	cd backend && uv run python tasks.py test
