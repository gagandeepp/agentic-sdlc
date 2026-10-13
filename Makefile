.PHONY: up down fmt lint test

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
