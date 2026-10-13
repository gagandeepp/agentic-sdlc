"""Role definitions (model, effort, tools, prompt, artifact) loaded from config/agents/*.yaml."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.artifacts import ARTIFACT_MODELS
from app.events.models import ArtifactKind, Role
from app.paths import CONFIG_DIR, PROMPTS_DIR

KNOWN_TOOLS = frozenset({"Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "Bash"})


class RoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Role
    model: str
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    tools: list[str]
    allow: list[str] | None = None  # permission rules, e.g. "Bash(git diff:*)"; defaults to tools
    max_turns: int = Field(ge=1)
    timeout_s: int = Field(ge=1)
    prompt: str
    output: ArtifactKind
    prompts_dir: Path = PROMPTS_DIR

    @field_validator("tools")
    @classmethod
    def _known_tools(cls, tools: list[str]) -> list[str]:
        unknown = set(tools) - KNOWN_TOOLS
        if unknown:
            raise ValueError(f"unknown tools: {sorted(unknown)}")
        return tools

    @property
    def allowed_tools(self) -> list[str]:
        return list(self.allow) if self.allow is not None else list(self.tools)

    @property
    def system_prompt(self) -> str:
        return (self.prompts_dir / self.prompt).read_text()

    @property
    def output_model(self) -> type[BaseModel]:
        return ARTIFACT_MODELS[self.output]


def load_roles(
    config_dir: Path = CONFIG_DIR / "agents", prompts_dir: Path = PROMPTS_DIR
) -> dict[Role, RoleConfig]:
    roles: dict[Role, RoleConfig] = {}
    for path in sorted(config_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        config = RoleConfig.model_validate({**raw, "prompts_dir": prompts_dir})
        if not (prompts_dir / config.prompt).is_file():
            raise FileNotFoundError(f"{path.name}: prompt {config.prompt} not found")
        roles[config.role] = config
    return roles
