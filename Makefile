VENV ?= .venv
PY := $(VENV)/bin/python
API := services/api
WEB := apps/web
TEST_DB ?= postgresql+psycopg://sdlc:sdlc@localhost:5432/sdlc_test

.PHONY: setup migrate seed api worker web test test-api test-web lint typecheck openapi evals check

setup:            ## install backend (editable) and frontend dependencies
	python3 -m venv $(VENV)
	$(PY) -m pip install -e "$(API)[dev]"
	cd $(WEB) && npm ci

migrate:
	cd $(API) && ../../$(VENV)/bin/alembic upgrade head

seed:
	$(VENV)/bin/agentic-sdlc seed

api:
	$(VENV)/bin/agentic-sdlc api --port 8000

worker:
	$(VENV)/bin/agentic-sdlc worker

web:
	cd $(WEB) && npm run dev

test: test-api test-web

test-api:
	cd $(API) && DATABASE_URL=$(TEST_DB) ../../$(PY) -m pytest

test-web:
	cd $(WEB) && npm test

lint:
	cd $(API) && ../../$(VENV)/bin/ruff check src tests migrations && ../../$(VENV)/bin/ruff format --check src tests migrations
	cd $(WEB) && npm run lint && npm run format:check

typecheck:
	cd $(API) && ../../$(VENV)/bin/mypy src
	cd $(WEB) && npm run typecheck

openapi:          ## regenerate OpenAPI document and the typed web client
	cd $(API) && APP_ENV=test ../../$(VENV)/bin/agentic-sdlc openapi > openapi.json
	cd $(WEB) && npm run gen:api

evals:
	cd $(API) && DATABASE_URL=$(TEST_DB) ../../$(PY) -m agentic_sdlc.evals.run --all

check: lint typecheck test
