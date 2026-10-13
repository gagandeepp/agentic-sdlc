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
| Live agent test | `cd api && RUN_LIVE=1 uv run pytest tests/live` (needs `AGENT_ANTHROPIC_KEY` in `.env`; spends about $0.10–0.30) |

## Agents (M3)

Each SDLC role runs as a Claude Agent SDK session (the bundled Claude Code CLI):

- **No keys in agents.** In the container, sessions run as a separate `agent` user with an allowlisted environment. They reach the API only through a local credential proxy, using a placeholder token.
- **Exact metering.** The proxy reads usage from every response, emits `agent.usage` and `budget.updated` events (tokens, cost, context fill), and stops a run at its budget.
- **NeMo Guardrails** checks every Bash command (YARA rules: pushes, env dumps, `sudo`, `curl | sh`, ...) and scans tool output for prompt injection. File tools are confined to the workspace.
- **Fail closed outside Docker.** Without the separate `agent` user, roles that can run shell commands refuse to start unless `ALLOW_UNISOLATED_BASH=true`.

Try one real agent (needs `AGENT_ANTHROPIC_KEY` in `.env`, costs about $0.10):

```bash
docker compose up --build -d
curl -s -X POST localhost:5173/api/dev/agent-probe -H 'content-type: application/json' \
  -d '{"role": "ba", "requirement": "Add a low-stock report"}'
```

Open the returned run at http://localhost:5173/runs/<id>.
