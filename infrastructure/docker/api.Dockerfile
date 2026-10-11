# API and worker image. Build context: repository root.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends git util-linux \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/api/pyproject.toml services/api/alembic.ini ./services/api/
COPY services/api/src ./services/api/src
COPY services/api/migrations ./services/api/migrations
COPY skills ./skills
COPY samples ./samples
RUN pip install ./services/api
# The sandbox uid used by the namespace backend. The worker needs CAP_SYS_ADMIN for unshare
# (see docker-compose.yml); the API container does not.
RUN useradd --system --uid 64000 --no-create-home --shell /usr/sbin/nologin sandbox \
 && useradd --system --uid 10001 --create-home app
WORKDIR /app/services/api
ENV DATA_DIR=/data SKILLS_DIR=/app/skills FIXTURES_DIR=/app/samples
EXPOSE 8000
CMD ["uvicorn", "agentic_sdlc.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
