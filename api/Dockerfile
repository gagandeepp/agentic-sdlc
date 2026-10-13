FROM node:22-slim AS node

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git sudo procps \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /bin/
# Node 22 for target repos' JS/TS toolchains (agents run npm, npx and corepack).
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack
# /work is sticky: agents (group sdlc) can't rename or replace what the API owns there.
# runs/ and git/ are API-owned and group read-only; each run's repo/ inside is agent-writable.
RUN groupadd --system sdlc \
    && useradd --create-home --groups sdlc appuser \
    && useradd --create-home --groups sdlc --shell /usr/sbin/nologin agent \
    && install -d -o root -g sdlc -m 3775 /work \
    && install -d -o agent -g sdlc -m 2775 /work/agent-home \
    && install -d -o appuser -g sdlc -m 2750 /work/runs /work/git
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY config ./config
COPY prompts ./prompts
COPY docker/sudoers /etc/sudoers.d/agentic
COPY docker/agent-cli docker/entrypoint.sh /usr/local/bin/
RUN chmod 0440 /etc/sudoers.d/agentic \
    && chmod 0755 /usr/local/bin/agent-cli /usr/local/bin/entrypoint.sh \
    && chown -R appuser /app
ENV PATH="/app/.venv/bin:$PATH" PORT=8080 WORKSPACE_ROOT=/work AGENT_USER=agent
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
