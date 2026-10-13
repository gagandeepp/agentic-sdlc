"""Structured outputs each role returns, and their human-readable markdown."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.events.models import ArtifactKind


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserStory(Artifact):
    id: str
    as_a: str
    i_want: str
    so_that: str
    acceptance_criteria: list[str] = Field(min_length=1)


class Requirements(Artifact):
    summary: str
    stories: list[UserStory] = Field(min_length=1)
    assumptions: list[str] = []
    open_questions: list[str] = []


class DesignTask(Artifact):
    id: str
    title: str
    description: str
    files: list[str]
    depends_on: list[str] = []
    acceptance_refs: list[str] = []


class Design(Artifact):
    overview_md: str
    components: list[str] = []
    tasks: list[DesignTask] = Field(min_length=1)


class DevResult(Artifact):
    summary: str
    task_ids: list[str]
    files_changed: list[str]
    notes: str = ""


class ReviewComment(Artifact):
    file: str
    line: int | None = None
    severity: Literal["blocker", "major", "minor", "nit"]
    body: str


class Review(Artifact):
    verdict: Literal["approve", "changes_requested"]
    summary: str
    comments: list[ReviewComment] = []


class TestReport(Artifact):
    __test__ = False  # not a pytest test class
    passed: bool
    summary: str
    commands_run: list[str]
    tests_added: list[str] = []
    failures: list[str] = []
    coverage_notes: str = ""


class ReleasePlan(Artifact):
    pr_title: str = Field(max_length=120)
    pr_body_md: str
    changelog_md: str
    ci_notes: str = ""


ARTIFACT_MODELS: dict[ArtifactKind, type[BaseModel]] = {
    ArtifactKind.REQUIREMENTS: Requirements,
    ArtifactKind.DESIGN: Design,
    ArtifactKind.DEV_RESULT: DevResult,
    ArtifactKind.REVIEW: Review,
    ArtifactKind.TEST_REPORT: TestReport,
    ArtifactKind.RELEASE_PLAN: ReleasePlan,
}


def output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON Schema with `$ref`s inlined: Claude Code wants a self-contained schema."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(definitions[node["$ref"].rsplit("/", 1)[-1]])
            return {key: inline(value) for key, value in node.items()}
        if isinstance(node, list):
            return [inline(value) for value in node]
        return node

    result: dict[str, Any] = inline(schema)
    return result


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _requirements_md(artifact: Requirements) -> str:
    parts = [f"# Requirements\n\n{artifact.summary}\n\n## User stories"]
    for story in artifact.stories:
        parts.append(
            f"### {story.id}\n\n"
            f"As a {story.as_a}, I want {story.i_want}, so that {story.so_that}.\n\n"
            f"Acceptance criteria:\n\n{_bullets(story.acceptance_criteria)}"
        )
    if artifact.assumptions:
        parts.append(f"## Assumptions\n\n{_bullets(artifact.assumptions)}")
    if artifact.open_questions:
        parts.append(f"## Open questions\n\n{_bullets(artifact.open_questions)}")
    return "\n\n".join(parts) + "\n"


def _design_md(artifact: Design) -> str:
    parts = [f"# Design\n\n{artifact.overview_md}"]
    if artifact.components:
        parts.append(f"## Components\n\n{_bullets(artifact.components)}")
    parts.append("## Tasks")
    for task in artifact.tasks:
        files = ", ".join(f"`{path}`" for path in task.files)
        lines = [f"### {task.id}: {task.title}", "", task.description, "", f"Files: {files}"]
        if task.depends_on:
            lines.append("Depends on: " + ", ".join(task.depends_on))
        if task.acceptance_refs:
            lines.append("Covers: " + ", ".join(task.acceptance_refs))
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def _dev_result_md(artifact: DevResult) -> str:
    notes = f"\n\n## Notes\n\n{artifact.notes}" if artifact.notes else ""
    return (
        f"# Implementation\n\n{artifact.summary}\n\nTasks: {', '.join(artifact.task_ids)}\n\n"
        f"## Files changed\n\n{_bullets(artifact.files_changed)}{notes}\n"
    )


def _review_md(artifact: Review) -> str:
    title = "approved" if artifact.verdict == "approve" else "changes requested"
    comments = [
        f"- **{c.severity}** `{c.file}{f':{c.line}' if c.line else ''}`: {c.body}"
        for c in artifact.comments
    ]
    body = "\n\n## Comments\n\n" + "\n".join(comments) if comments else ""
    return f"# Review: {title}\n\n{artifact.summary}{body}\n"


def _test_report_md(artifact: TestReport) -> str:
    outcome = "passed" if artifact.passed else "failed"
    commands = _bullets([f"`{command}`" for command in artifact.commands_run])
    parts = [f"# Test report: {outcome}\n\n{artifact.summary}", f"## Commands\n\n{commands}"]
    if artifact.tests_added:
        parts.append(f"## Tests added\n\n{_bullets(artifact.tests_added)}")
    if artifact.failures:
        parts.append(f"## Failures\n\n{_bullets(artifact.failures)}")
    if artifact.coverage_notes:
        parts.append(f"## Coverage notes\n\n{artifact.coverage_notes}")
    return "\n\n".join(parts) + "\n"


def _release_plan_md(artifact: ReleasePlan) -> str:
    ci = f"\n\n## CI\n\n{artifact.ci_notes}" if artifact.ci_notes else ""
    return (
        f"# {artifact.pr_title}\n\n{artifact.pr_body_md}\n\n"
        f"## Changelog\n\n{artifact.changelog_md}{ci}\n"
    )


def render_markdown(kind: ArtifactKind, artifact: BaseModel) -> str:
    if isinstance(artifact, Requirements):
        return _requirements_md(artifact)
    if isinstance(artifact, Design):
        return _design_md(artifact)
    if isinstance(artifact, DevResult):
        return _dev_result_md(artifact)
    if isinstance(artifact, Review):
        return _review_md(artifact)
    if isinstance(artifact, TestReport):
        return _test_report_md(artifact)
    if isinstance(artifact, ReleasePlan):
        return _release_plan_md(artifact)
    raise ValueError(f"no markdown renderer for {kind}")
