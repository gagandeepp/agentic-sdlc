# agentic-sdlc

A team of Claude agents takes a free-text requirement to a GitHub pull request, the way a manual SDLC does: BA → Architect → parallel Devs → Integrator → Reviewer → QA → DevOps, with human approval gates. A React "mission control" GUI shows the run live: agent interactions, the messages passed between agents, context size, and input/output tokens.

> **Status:** under construction. Progress is tracked in the [milestones](https://github.com/gagandeepp/agentic-sdlc/wiki/Milestones).

- Design spec: [docs/superpowers/specs/2026-10-13-agentic-sdlc-design.md](docs/superpowers/specs/2026-10-13-agentic-sdlc-design.md)
- Design discussion: [#1](https://github.com/gagandeepp/agentic-sdlc/discussions/1)

## Quick start

Requires Docker.

```bash
cp .env.example .env
docker compose up --build
```

Open http://localhost:5173. API health: http://localhost:8080/api/health.

## Development

| | |
|---|---|
| API | `docker compose up -d mongo`, then `cd api && uv sync && uv run uvicorn app.main:app --reload --port 8080` |
| Web | `cd web && corepack pnpm install && corepack pnpm dev` (proxies `/api` to :8080) |
| Tests | `make test` (API tests start MongoDB with testcontainers, so Docker must be running) |
| Lint / types | `make lint` |
