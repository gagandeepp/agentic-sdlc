from app.orchestrator.prompts import (
    Fix,
    Revision,
    architect_prompt,
    ba_prompt,
    dev_prompt,
    devops_prompt,
    qa_prompt,
    reviewer_prompt,
)


def test_ba_prompt_carries_the_request_the_repo_and_a_revision() -> None:
    prompt = ba_prompt(
        "Add a /health endpoint",
        "Files:\napp.py",
        revision=Revision("# Requirements\nold", "Split story 2"),
    )
    assert "Add a /health endpoint" in prompt and "app.py" in prompt
    assert "# Requirements\nold" in prompt and "Split story 2" in prompt
    assert "previous draft" not in ba_prompt("x", "y")


def test_architect_prompt_uses_the_approved_requirements() -> None:
    prompt = architect_prompt("# Requirements (edited)", "Files:\napp.py")
    assert "# Requirements (edited)" in prompt and "app.py" in prompt


def test_dev_prompt_uses_edited_markdown_and_fix_details() -> None:
    prompt = dev_prompt(
        "# Requirements",
        "# Design (edited by a person)",
        fix=Fix("review", "- add input validation"),
    )
    assert "(edited by a person)" in prompt and "add input validation" in prompt
    assert "reviewer requested changes" in prompt and "already committed" in prompt
    assert "Implement the approved design" in dev_prompt("r", "d")
    assert "QA found failures" in dev_prompt("r", "d", fix=Fix("qa", "test_x failed"))


def test_review_qa_and_devops_prompts_include_the_diff() -> None:
    diff = "diff --git a/app.py b/app.py\n+print('health')\n```\n"
    review = reviewer_prompt("# R", "# D", diff, "abc123")
    assert "git diff abc123" in review and "+print('health')" in review
    assert "````diff" in review  # a fence longer than any backtick run in the diff
    assert "+print('health')" in qa_prompt("# R", "# D", diff)
    devops = devops_prompt("# R", "# D", diff, review_md="# Review: approved", test_report_md=None)
    assert "# Review: approved" in devops and "Test report" not in devops
