"""Task prompts for each role, built from artifact markdown so a person's edits flow downstream.

The role's system prompt (api/prompts/<role>.md) says how to work. These say what to work on.
"""

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Revision:
    """A person asked for changes to the previous draft at a gate."""

    previous_markdown: str
    feedback: str


@dataclass(frozen=True)
class Fix:
    source: Literal["review", "qa", "human"]
    details: str


_FIX_INTROS = {
    "review": "The code reviewer requested changes. Address every comment:",
    "qa": "QA found failures. Fix the code (change a test only if the test itself is wrong):",
    "human": "A person reviewing this run asked for changes:",
}


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip()}\n"


def _code(body: str, language: str = "") -> str:
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{body.rstrip()}\n{fence}"


def _join(*parts: str) -> str:
    return "\n".join(part for part in parts if part)


def _revision(revision: Revision | None) -> str:
    if revision is None:
        return ""
    return _join(
        _section("Your previous draft", revision.previous_markdown),
        _section("Requested changes (from a person)", revision.feedback),
    )


def ba_prompt(requirement: str, repo_summary: str, *, revision: Revision | None = None) -> str:
    return _join(
        "Write the requirements for this change request.\n",
        _section("Change request", requirement),
        _section("Repository overview", repo_summary),
        _revision(revision),
    )


def architect_prompt(
    requirements_md: str, repo_summary: str, *, revision: Revision | None = None
) -> str:
    return _join(
        "Design the approved requirements and split the work into tasks.\n",
        _section("Approved requirements", requirements_md),
        _section("Repository overview", repo_summary),
        _revision(revision),
    )


def dev_prompt(requirements_md: str, design_md: str, *, fix: Fix | None = None) -> str:
    if fix is None:
        intro = "Implement the approved design, with tests.\n"
        change = ""
    else:
        intro = "Continue the work on this branch; your earlier changes are already committed.\n"
        change = _section("What to change now", f"{_FIX_INTROS[fix.source]}\n\n{fix.details}")
    return _join(
        intro,
        _section("Approved requirements", requirements_md),
        _section("Approved design", design_md),
        change,
    )


def reviewer_prompt(requirements_md: str, design_md: str, diff: str, base_sha: str) -> str:
    return _join(
        f"Review the change on this branch. `git diff {base_sha}` shows all of it; "
        "the diff is also below.\n",
        _section("Approved requirements", requirements_md),
        _section("Approved design", design_md),
        _section("Diff", _code(diff, "diff")),
    )


def qa_prompt(requirements_md: str, design_md: str, diff: str) -> str:
    return _join(
        "Verify that the change meets every acceptance criterion: add any missing tests and "
        "run the full test suite.\n",
        _section("Approved requirements", requirements_md),
        _section("Approved design", design_md),
        _section("Diff", _code(diff, "diff")),
    )


def devops_prompt(
    requirements_md: str,
    design_md: str,
    diff: str,
    *,
    review_md: str | None = None,
    test_report_md: str | None = None,
) -> str:
    return _join(
        "Prepare the pull request for this change.\n",
        _section("Approved requirements", requirements_md),
        _section("Approved design", design_md),
        _section("Code review", review_md) if review_md else "",
        _section("Test report", test_report_md) if test_report_md else "",
        _section("Diff", _code(diff, "diff")),
    )
