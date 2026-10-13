from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from app.agents.artifacts import ARTIFACT_MODELS, Requirements, output_schema, render_markdown
from app.agents.roles import KNOWN_TOOLS, RoleConfig, load_roles
from app.events.models import ArtifactKind, Role

SAMPLES: dict[ArtifactKind, dict[str, Any]] = {
    ArtifactKind.REQUIREMENTS: {
        "summary": "CSV export",
        "stories": [
            {
                "id": "US-1",
                "as_a": "manager",
                "i_want": "a CSV",
                "so_that": "I can share it",
                "acceptance_criteria": ["Download returns text/csv"],
            }
        ],
    },
    ArtifactKind.DESIGN: {
        "overview_md": "Add an endpoint.",
        "tasks": [
            {"id": "T1", "title": "Endpoint", "description": "Add /export", "files": ["api.py"]}
        ],
    },
    ArtifactKind.DEV_RESULT: {
        "summary": "Added endpoint",
        "task_ids": ["T1"],
        "files_changed": ["api.py"],
    },
    ArtifactKind.REVIEW: {
        "verdict": "changes_requested",
        "summary": "One issue",
        "comments": [
            {"file": "api.py", "line": 3, "severity": "major", "body": "Handle empty list"}
        ],
    },
    ArtifactKind.TEST_REPORT: {
        "passed": True,
        "summary": "All green",
        "commands_run": ["uv run pytest"],
    },
    ArtifactKind.RELEASE_PLAN: {
        "pr_title": "Add CSV export",
        "pr_body_md": "Adds export.",
        "changelog_md": "- CSV export",
    },
}


def _walk(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


@pytest.mark.parametrize("kind", list(SAMPLES))
def test_output_schemas_are_self_contained_draft7_and_strict(kind: ArtifactKind) -> None:
    schema = output_schema(ARTIFACT_MODELS[kind])
    nodes = _walk(schema)
    assert not any("$ref" in node or "$defs" in node for node in nodes)
    objects = [node for node in nodes if node.get("type") == "object"]
    assert all(node.get("additionalProperties") is False for node in objects)
    jsonschema.Draft7Validator.check_schema(schema)
    jsonschema.Draft7Validator(schema).validate(SAMPLES[kind])


@pytest.mark.parametrize("kind", list(SAMPLES))
def test_every_artifact_renders_markdown(kind: ArtifactKind) -> None:
    artifact = ARTIFACT_MODELS[kind].model_validate(SAMPLES[kind])
    markdown = render_markdown(kind, artifact)
    assert markdown.startswith("# ")
    assert len(markdown.splitlines()) > 2


def test_requirements_need_at_least_one_story() -> None:
    with pytest.raises(ValidationError):
        Requirements.model_validate({"summary": "x", "stories": []})


def test_roles_load_with_the_planned_model_tiers() -> None:
    roles = load_roles()
    assert set(roles) == {Role.BA, Role.ARCHITECT, Role.DEV, Role.REVIEWER, Role.QA, Role.DEVOPS}
    assert roles[Role.ARCHITECT].model == roles[Role.REVIEWER].model == "claude-opus-5-5"
    sonnet_roles = (Role.BA, Role.DEV, Role.QA)
    assert {roles[role].model for role in sonnet_roles} == {"claude-sonnet-5-5"}
    assert roles[Role.DEVOPS].model == "claude-haiku-5-5"
    assert "Bash" not in roles[Role.BA].tools and "Bash" not in roles[Role.ARCHITECT].tools
    assert roles[Role.REVIEWER].allowed_tools == [
        "Read",
        "Glob",
        "Grep",
        "Bash(git diff:*)",
        "Bash(git log:*)",
        "Bash(git show:*)",
        "Bash(git status:*)",
    ]
    for role in roles.values():
        assert set(role.tools) <= KNOWN_TOOLS
        assert role.system_prompt.strip()
        assert role.output_model is ARTIFACT_MODELS[role.output]


def test_unknown_tool_names_are_rejected() -> None:
    with pytest.raises(ValidationError):
        RoleConfig.model_validate(
            {
                "role": "ba",
                "model": "claude-sonnet-5-5",
                "effort": "medium",
                "tools": ["Reed"],
                "max_turns": 5,
                "timeout_s": 60,
                "prompt": "ba.md",
                "output": "requirements",
            }
        )


def test_every_role_may_install_dependencies_but_not_browse() -> None:
    for role in load_roles().values():
        assert "don't browse the web" in role.system_prompt
        assert "no network access" not in role.system_prompt
