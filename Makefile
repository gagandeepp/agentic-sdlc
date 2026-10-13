.PHONY: up down fmt lint test types fixtures smoke

# corepack ships with Node and honours web/package.json's packageManager pin
PNPM ?= corepack pnpm

up:
	docker compose up --build -d

down:
	docker compose down

fmt:
	cd api && uv run ruff format . && uv run ruff check --fix .

lint:
	cd api && uv run ruff check . && uv run ruff format --check . && uv run mypy app
	cd web && $(PNPM) lint && $(PNPM) typecheck

test:
	cd api && uv run pytest
	cd web && $(PNPM) test

types:
	cd api && uv run python -m app.schema > ../web/src/types/api.schema.json
	cd web && $(PNPM) gen:types

fixtures:
	cd api && uv run python -m app.dev.export_fixture > ../fixtures/demo-runs/fake-basic.jsonl

smoke:
	cd api && RUN_LIVE=1 uv run pytest tests/live -q -p no:warnings
