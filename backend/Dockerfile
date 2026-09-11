# syntax=docker/dockerfile:1
#
# Build context is the REPO ROOT, not backend/ (see docker-compose.yml).
# The image mirrors the repo's own layout under /srv, because several paths
# in the code are computed relative to a file's own location and would
# otherwise resolve to nothing:
#   - app/config.py reads ../../.env  ->  /srv/.env
#   - scripts/ingest_dataset.py reads ../corporate_rag_dataset and puts
#     ../backend on sys.path  ->  /srv/corporate_rag_dataset, /srv/backend
# Flattening it all into one directory would mean patching those, which is
# a worse trade than keeping the tree shape.

# uv comes from its own image rather than pip: faster, and it keeps the
# tool out of the project environment it is managing.
FROM ghcr.io/astral-sh/uv:0.9.18 AS uv

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/srv/backend/.venv \
    PATH=/srv/backend/.venv/bin:$PATH

COPY --from=uv /uv /usr/local/bin/uv

# postgresql-client supplies pg_isready and psql, which the entrypoint uses
# to wait for the database and create it if it is missing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/backend

# Dependencies resolve from the lockfile in their own layer, so editing
# application code does not re-download everything. --no-install-project
# lets this run before the source is copied; --no-dev keeps pytest and
# friends out of the runtime image.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY backend/alembic.ini backend/tasks.py ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY backend/scripts ./scripts

RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# Repo-root siblings the code reaches for by relative path.
COPY scripts /srv/scripts
COPY corporate_rag_dataset /srv/corporate_rag_dataset

COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Strip CR before making it executable. .gitattributes now pins this file to
# LF, but that only governs a FRESH checkout -- a tree cloned on Windows
# before it existed still holds CRLF, and the shebang then names an
# interpreter called "bash\r":
#     env: 'bash\r': No such file or directory
# One sed here means the image builds from either tree, so nobody has to
# re-clone to unbreak themselves.
# The trailing grep is an assertion, not decoration: if a CR ever survives
# this step, the BUILD fails here with a clear message, instead of shipping
# an image whose only symptom is a container restart-looping on
# "env: 'bash\r': No such file or directory" -- which reads like a code bug
# and is really a checkout artefact.
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh \
    && head -1 /usr/local/bin/docker-entrypoint.sh | grep -q 'bash$' \
    && echo "entrypoint line endings OK"

# BACKEND_HOST defaults to 127.0.0.1, which is right on a laptop and wrong
# in a container: bound to its own loopback, nothing reaches the published
# port. 0.0.0.0 here is the container's boundary, not a widened one.
ENV BACKEND_HOST=0.0.0.0 \
    BACKEND_PORT=8080 \
    ATTACHMENT_STORAGE_DIR=/srv/backend/storage/uploads

EXPOSE 8080

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
