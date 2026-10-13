# agentic-sdlc: design spec

_Status: approved, 2026-10-13_

## Context

Build a **real dev tool** (also a public GitHub portfolio piece). You type a requirement in free text. A team of Claude agents (BA → Architect → parallel Devs → Integrator → Reviewer → QA → DevOps) delivers a **GitHub PR**, mimicking a manual SDLC with human approval gates. A React SPA visualizes the run: an animated agent graph, every message and tool call between agents, and per-agent and per-turn metrics (context fill, input/output/cache tokens, cost). It is Dockerized, hosted for free, and anyone can replay recorded runs. Visitors who bring their own keys can run it live.

Greenfield project. Container runtime: Python 3.12, Node 22 (web build only).

## Decisions

| Topic | Decision |
|---|---|
| Purpose | Real dev tool + public portfolio |
| Roles | BA/PO, Architect/Tech Lead, Developer(s), Integrator, Reviewer, QA, DevOps |
| Topology | Orchestrated pipeline (central state machine) |
| Human gates | After BA, after Architect, before PR is marked ready. Actions: **Approve / Request changes (feedback → phase re-runs) / Edit artifact / Abort** |
| Runtime | Claude Agent SDK for **Python** (`claude-agent-sdk`, bundles the CLI, no Node in the image) |
| Models | Tiered, set in YAML: Opus 5.5 (`claude-opus-5-5`) for Architect and Reviewer; Sonnet 5.5 (`claude-sonnet-5-5`) for BA, Dev, Integrator, and QA; Haiku 5.5 (`claude-haiku-5-5`) for DevOps |
| Input → output | Free-text requirement in GUI → GitHub PR |
| Target repo stacks (v1) | Python + Node/TS toolchains in the image |
| Parallel devs | Yes: git worktrees, max 3 (configurable). File ownership declared by Architect; tasks with overlapping files are serialized; **Integrator** agent resolves leftover conflicts |
| Artifacts | Structured JSON in Mongo (rendered in GUI) **and** markdown in PR under `docs/sdlc/<run-id>/` |
| Guardrails | Per-run $ budget cap, loop limits (Dev⇄Review ≤3, QA→Dev ≤2, then escalate to gate), **NeMo Guardrails** on Bash commands, tool results, user input and published output, wall-clock timeout (~50 min run excluding gate waits, per-phase caps) |
| Engine | Event-sourced, in-process asyncio state machine in FastAPI. One event log feeds both live view and replay |
| Store | MongoDB (local container; Atlas M0 when hosted) |
| Frontend | React + Vite + TS, React Flow (`@xyflow/react`), Recharts, Tailwind + shadcn/ui; "mission control" layout |
| Public access | Replay recorded runs for free, or **BYO Anthropic key + GitHub token** for live runs (keys held in memory only, never persisted) |
| Hosting | SPA → **Vercel**; API container → **GCP Cloud Run**; DB → **MongoDB Atlas M0**; local → `docker compose up` |

## Architecture

```
 Browser (Vercel SPA) ──REST──▶ FastAPI (Cloud Run container) ──▶ MongoDB (Atlas)
        ▲                         │  Orchestrator (asyncio state machine)
        └────────SSE events───────┤  EventBus → append to Mongo `events` + fan out to SSE
                                  │  AgentRunner ─spawns─▶ Agent SDK sessions (cwd = worktree)
                                  │        │ ANTHROPIC_BASE_URL=http://127.0.0.1:<p>/r/<nonce>
                                  │        ▼
                                  │  CredentialProxy (injects real key, taps usage, enforces budget) ──▶ api.anthropic.com
                                  │  GitOps (clone/push/PR via token, never exposed to agents) ──▶ GitHub
```

### Security model (key design point)

With Bash enabled, an agent's shell inherits its env, so a prompt injection could exfiltrate keys. Therefore:
- **Separate uid** (revised 2026-10-13): the Claude Code CLI runs as a dedicated `agent` user through a sudo wrapper (`cli_path`) that passes only an allowlist of env vars (`env -i`). An agent can't read the API process's `/proc/*/environ`, the secrets files, or the git process env, and every `agent` process is killed after each session. The API process scrubs key env vars at entrypoint.
- **Anthropic key**: agents get `ANTHROPIC_BASE_URL` pointing at a **CredentialProxy** on its own loopback port (no path prefix), plus a per-session placeholder bearer token (`ANTHROPIC_AUTH_TOKEN`). The proxy maps token → real key (in memory) and forwards to Anthropic, streaming. Only `POST /v1/messages` (metered), `POST /v1/messages/count_tokens` and `/api/hello` are routed; everything else is 404, and each grant has a model allowlist. The proxy **parses `message_start`/`message_delta` usage** on every request. That makes it the authoritative token/cost tap, including subagent calls, and enforces a **hard budget cap** (stronger than the SDK's client-side `max_budget_usd`, which we also set).
- **GitHub token**: used only by `GitOps` Python code, through a one-shot credential helper in the git process env. It is never written to `.git/config`, and network git operations run only when no `agent` process exists. Agents cannot push; DevOps produces structured PR content and the orchestrator executes it.
- **Git dir outside the agent's reach** (added 2026-10-13, security review): the agent uid can write the working tree (shared `sdlc` group), so it could plant `.git/hooks/*` or `.git/config` entries (`core.fsmonitor`, filters, credential helpers) that would run as the API user on the next git command. Therefore:
  - GitOps clones with `--separate-git-dir` into an API-owned directory outside the working tree (group `sdlc`, read-only to the group: `2750`/`0640`). Agents can still run `git diff`/`log`/`show`, but they cannot write the git dir, so agents never commit.
  - Every API-side git command sets `GIT_DIR` and `GIT_WORK_TREE` explicitly, ignoring any `.git` file the agent might replace. It also sets `GIT_CONFIG_NOSYSTEM=1` and `GIT_CONFIG_GLOBAL=/dev/null`, and passes `-c core.hooksPath=/dev/null -c core.fsmonitor=false`.
  - The API never executes anything from the working tree.
- **Local development without the agent uid**: Bash-capable roles (Dev, Reviewer, QA, DevOps) refuse to start unless `AGENT_USER` is set, or `ALLOW_UNISOLATED_BASH=true` explicitly accepts the risk. The runner also blanks known secret variables in the CLI's environment.
- **Guardrails: NeMo Guardrails** (`check_async`, rails only, no main-LLM call) on four surfaces:
  - **Bash commands:** a `PreToolUse` hook runs custom YARA rules through NeMo injection detection. It fails closed.
  - **Tool results:** a `PostToolUse` scan for indirect prompt injection. It fails open with a warning event.
  - **User input:** the requirement and gate feedback get a Claude Haiku `self check input` plus context-bloat limits, metered through the proxy as a "guardrails" agent.
  - **Publish:** everything sent to GitHub gets Presidio masking of secrets/PII and XSS/template injection removal.

  File tools are confined to the workspace by a Python `realpath`/`commonpath` check, which also denies `.github/workflows/**`. Each denial emits a `guardrail.blocked` event. These are backstops; the boundary is the container plus the separate uid. Use `permission_mode="dontAsk"` with scoped `allowed_tools` (`bypassPermissions` refuses to run as root).
- Hosted mode: Cloud Run `max-instances=1`, 1 live run at a time (others get "busy" and can still watch replays), per-IP rate limit, shallow clone, and a repo size cap.

## Workflow state machine

```
INIT → BA → [GATE requirements] → ARCHITECT → [GATE design]
     → DEV (scheduler: parallel worktrees) → INTEGRATE (merge in order; Integrator agent on conflict)
     → REVIEW ⇄ DEV_FIX (≤3, else escalate to gate)
     → QA ⇄ DEV_FIX (≤2, else escalate) → push run branch
     → DEVOPS (PR content + docs/sdlc/<run-id>/*.md, draft PR opened, CI checks polled)
     → [GATE release] → mark PR ready → DONE
Any state → ABORTED | FAILED (budget/timeout/error) | INTERRUPTED (container recycled; resumable)
```

- Every gate sits at a **durable point**: BA and Architect artifacts are in Mongo, and code is on the pushed run branch. A run interrupted by a Cloud Run recycle resumes from the last checkpoint. Hosted mode asks for keys again on resume.
- "Request changes" re-invokes the same role with prior artifact + feedback (new graph edge, `loop.iteration` event).
- Merging the PR stays a human action on GitHub.

### Agent contracts (structured output via `output_format` json_schema → `ResultMessage.structured_output`)

| Role | Input | Output artifact (Pydantic model) |
|---|---|---|
| BA | requirement, repo summary | `Requirements{stories[{id,as_a,i_want,so_that,acceptance_criteria[]}], assumptions, open_questions}` |
| Architect | Requirements, repo | `Design{overview_md, components, tasks[{id,title,description,files[],depends_on[],acceptance_refs[]}]}` |
| Dev | one task (or review/QA feedback), worktree | `DevResult{task_id, summary, files_changed[], notes}` |
| Integrator | conflicted merge state | `IntegrationResult{resolved_files[], notes}` |
| Reviewer | merged diff, Design, Requirements | `Review{verdict: approve|changes_requested, comments[{file,line,severity,body}]}` |
| QA | Requirements, code | `TestReport{tests_added[], commands_run[], passed, failures[], coverage_notes}` |
| DevOps | all artifacts, diff | `ReleasePlan{pr_title, pr_body_md, changelog_md, ci_notes}` |

Role definitions live in `config/agents/<role>.yaml` (model, allowed_tools, max_turns, phase timeout, prompt file) and `prompts/<role>.md`. Missing/invalid structured output (`error_max_structured_output_retries`, or success without `structured_output`) → phase failure event → escalate to gate.

### Parallel dev scheduler
Tasks form a DAG from `depends_on` plus file-overlap edges. Up to `max_parallel_devs` ready tasks run concurrently, each in `worktrees/task-<id>` on branch `sdlc/<run>/task-<id>`. Task branches merge into `sdlc/<run>` in topological order. On conflict, the Integrator agent works on the run branch with conflict markers. Graph shows Dev1..N fan-out → Integrator fan-in.

## Event contract (the backbone: live, replay, metrics, and tests all use it)

Envelope: `{run_id, seq (monotonic per run), ts, type, phase?, agent_id?, payload}`. It is a Pydantic model and the single source of truth. TS types are generated (`pydantic → JSON Schema → json-schema-to-typescript` into `web/src/types/events.ts`), and CI fails if the generated types are stale.

Types: `run.created|status_changed|completed|failed|aborted|interrupted`, `phase.started|completed`, `agent.started|finished`, `agent.text`, `agent.thinking`, `agent.tool_call`, `agent.tool_result` (payload truncated >16 KB with flag), `agent.usage` (per turn: input, output, cache_creation, cache_read, context_tokens, context_max, cost_usd, model), `handoff` (agent→agent message with artifact ref; drives edge animation), `artifact.created|revised`, `gate.opened|resolved`, `loop.iteration`, `guardrail.blocked`, `budget.updated`, `merge.started|conflict|completed`, `git.pushed`, `pr.opened|ready`, `ci.status`.

Metrics sources:
- The proxy usage tap gives exact tokens and cost per API request.
- `AssistantMessage.usage` is deduped by `message_id`.
- `ClaudeSDKClient.get_context_usage()` after each turn gives context fill % (`totalTokens/maxTokens`).
- Prices come from `config/pricing.yaml`; verify current prices with the claude-api skill at build time.

## Persistence (Mongo)
- `runs`: requirement, repo, branch, config snapshot, status, current phase, budget, rollup totals per role (for history and compare).
- `events`: unique index `(run_id, seq)`.
- `artifacts`: run_id, kind, version, data, markdown.
- Demo runs are exported to `fixtures/demo-runs/*.jsonl` and seeded into Atlas by `make seed`.

## API (FastAPI)
- `POST /api/runs`: hosted mode takes keys in `X-Anthropic-Key` / `X-GitHub-Token` headers, held in memory only.
- `GET /api/runs`, `GET /api/runs/{id}`.
- `GET /api/runs/{id}/events?after=<seq>` (SSE: catch up from Mongo, then live). `GET /api/runs/{id}/events.jsonl` (full log for replay).
- `POST /api/runs/{id}/gates/{gate_id}` `{action: approve|revise|edit, feedback?, artifact?}`.
- `POST /api/runs/{id}/abort`, `POST /api/runs/{id}/resume`.
- `GET /api/compare?ids=`, `GET /api/health`.

SSE rather than WebSocket: server→client only, works through Cloud Run, auto-reconnects with `Last-Event-ID`=seq. Cloud Run only allocates CPU during a request, so live runs need a viewer connected. The UI says so, and a recycled container marks the run INTERRUPTED (resumable).

## Frontend (React SPA)
- **One reducer** `events → RunViewState` (Zustand store) for both live SSE and client-side replay. The replay player has play/pause, 1–16× speed, and a scrubber.
- **Mission control** (`/runs/:id`):
  - React Flow graph with role nodes showing status (idle, thinking, tool, waiting, done, failed), model badge, and a mini context bar. Custom edges animate a packet on `handoff`, and loop edges show `↺ n/3`.
  - Right: virtualized timeline, filterable by agent and type; clicking an entry shows the payload (markdown, JSON, or diff). Guardrail blocks show in red.
  - Bottom: metrics strip with the active agent's context %, in/out/cache tokens, cache-hit %, a cost-vs-budget gauge, and a tokens/turn sparkline.
  - Clicking a node opens an **inspector drawer** with per-turn token stacked bars, a context-fill line chart, and that agent's messages.
  - The gate panel shows artifact markdown with Approve, Request changes (feedback box), Edit (markdown editor), and Abort.
- `/`: runs list, featured demo replays, and "New run" (requirement, repo, base branch, budget, max devs; BYO keys kept in React state only).
- `/compare?ids=`: 2–4 runs compared on cost, tokens per role, duration, loop counts, and cache-hit ratio.
- Dark "mission control" theme with a light toggle. Charts follow the dataviz skill.

## Repo layout
```
agentic-sdlc/
  api/                    # Python 3.12, uv
    app/main.py           # FastAPI app, routes
    app/events/           # Pydantic event models, EventBus, Mongo event store
    app/orchestrator/     # state machine, phases, gates, scheduler, guardrails
    app/agents/           # AgentRunner (SDK wrapper → events), role loader, bash policy hook
    app/proxy/            # CredentialProxy + usage tap + budget enforcement
    app/gitops/           # clone/worktree/merge/push, GitHub REST (PR, checks)
    config/agents/*.yaml  config/pricing.yaml  prompts/*.md
    tests/
  web/                    # Vite + React + TS, pnpm
    src/types/events.ts (generated)  src/store/  src/features/{graph,timeline,metrics,gates,runs,compare,replay}/
  fixtures/demo-runs/     docs/superpowers/specs/  docs/architecture.md
  docker-compose.yml      api/Dockerfile  web/Dockerfile (nginx, local only)
  .github/workflows/{ci.yml,deploy-api.yml}   vercel.json   Makefile   README.md
```

## Deployment
- **Local**: `docker compose up` brings up `api`, `web` (nginx), and `mongo:8.0` (matches Atlas). Keys come from `.env`.
- **Cloud Run**:
  - Image built by GitHub Actions and pushed to Artifact Registry. Deploy uses Workload Identity Federation (no JSON keys).
  - Settings: 2 vCPU / 4 GiB, timeout 3600s, max-instances 1, request-based billing, `HOSTED_MODE=true`. The Mongo URI is in Secret Manager.
- **Atlas M0**: same region as Cloud Run; IP allowlist 0.0.0.0/0 with a strong user (M0 has no private networking). Document this trade-off.
- **Vercel**: GitHub integration, root `web/`, `VITE_API_URL` env, per-PR preview deploys. CORS allowlist on the API.
- README: architecture diagram, demo GIF, a "try a replay" link, a BYO-key guide (fine-grained PAT scoped to one repo), and a cost note. GCP needs a billing account, so set a $1 budget alert.

## Testing
- **Backend (pytest, pytest-asyncio)**:
  - A `FakeAgentRunner` scripts events and outputs to drive the state machine through gates, revise loops, loop-limit escalation, budget stop, timeout, abort, and resume.
  - Scheduler DAG and overlap tests.
  - GitOps tests against real temp git repos, including a forced merge conflict.
  - A table-driven bash-policy suite.
  - CredentialProxy tests with `respx` mocking Anthropic streaming responses (usage parsing, budget cutoff, key never echoed).
  - Mongo via testcontainers.
- **Frontend**: Vitest for the reducer (fixture JSONL → expected view state) and components; Playwright e2e replays a fixture run (graph animates, timeline fills, metrics totals match fixture sums).
- **Live smoke** (opt-in, `make smoke`, needs keys): a small requirement against the `agentic-sdlc-sandbox` repo (its app comes from a separate use case the owner will define) reaches a draft PR.
- **CI**: ruff, mypy, eslint, tsc, both test suites, generated-types freshness check, docker build.

## Milestones (each ends green and demoable)
1. **Skeleton**: repo, compose (api/web/mongo), health endpoint, Vite shell, CI.
2. **Event core**: event models + TS codegen, Mongo store, SSE endpoint, SPA reducer + timeline driven by FakeAgentRunner.
3. **Agent layer**: AgentRunner (SDK messages → events), CredentialProxy + usage tap, bash policy hook, role YAML + prompts + structured outputs.
4. **Orchestrator, single dev**: full state machine, gates, loops, budget, timeouts, GitOps clone/push/PR. First real PR on the sandbox repo.
5. **Parallel devs**: scheduler, worktrees, ordered merge, Integrator.
6. **GUI polish**: mission-control graph animations, metrics strip, inspector, gate UI, history, compare, replay player.
7. **Ship**: Cloud Run + Atlas + Vercel deploy workflows, hosted-mode hardening, record 2–3 demo runs → fixtures + seed, README/GIF.


## Verification (end to end)
- `docker compose up`, then open `http://localhost:5173`. Start a run against the sandbox repo with a small requirement ("add a /health endpoint with a test"). Watch the graph animate BA → Arch → Devs → Integrator → Review → QA → DevOps, with metrics updating per turn.
- Exercise each gate: Request changes (the phase re-runs with feedback), Edit (the edited artifact is used downstream), Approve. The run ends with a ready PR on GitHub containing code, tests, and `docs/sdlc/<run-id>/`.
- Force the guardrails:
  - A tiny budget stops at the cap.
  - A prompt asking for `git push` produces a `guardrail.blocked` event.
  - Killing the api container mid-run marks the run INTERRUPTED, and resume works.
- Compare the proxy-tap totals against the sum of `ResultMessage.model_usage` (within a small tolerance).
- Replay a finished run: the totals at the end of the replay match the live run, and the compare page shows the two runs side by side.
- Hosted: deploy to Cloud Run + Vercel and confirm a fresh browser can replay the seeded demos with no keys. A live run with BYO keys works, and a second concurrent run gets "busy".
