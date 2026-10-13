FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git sudo procps \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /bin/
RUN groupadd --system sdlc \
    && useradd --create-home --groups sdlc appuser \
    && useradd --create-home --groups sdlc --shell /usr/sbin/nologin agent \
    && install -d -o root -g sdlc -m 2775 /work \
    && install -d -o agent -g sdlc -m 2775 /work/agent-home
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
