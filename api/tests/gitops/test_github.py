from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.gitops.github import (
    CiStatus,
    GitHubError,
    HttpGitHubClient,
    PullRequest,
    summarize_checks,
)
from app.llm.server import EmbeddedServer

TOKEN = "ghp_" + "k" * 36
PULL = {"number": 7, "html_url": "https://github.com/o/r/pull/7", "node_id": "PR_7", "draft": True}


@dataclass
class FakeGitHubApi:
    requests: list[tuple[str, str, dict[str, str], Any]] = field(default_factory=list)
    pull_exists: bool = False
    check_runs: list[dict[str, Any]] = field(default_factory=list)
    graphql_errors: list[dict[str, str]] | None = None
    fail_with: int | None = None

    async def handle(self, request: Request) -> JSONResponse:
        raw = await request.body()
        body = await request.json() if raw else None
        self.requests.append((request.method, request.url.path, dict(request.headers), body))
        if self.fail_with:
            return JSONResponse({"message": "boom"}, status_code=self.fail_with)
        path, method = request.url.path, request.method
        if path == "/graphql":
            if self.graphql_errors:
                return JSONResponse({"errors": self.graphql_errors})
            return JSONResponse(
                {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}}
            )
        if path.endswith("/check-runs"):
            return JSONResponse(
                {"total_count": len(self.check_runs), "check_runs": self.check_runs}
            )
        if path == "/repos/o/r/pulls" and method == "POST":
            if self.pull_exists:
                return JSONResponse({"message": "Validation Failed"}, status_code=422)
            return JSONResponse(PULL, status_code=201)
        if path == "/repos/o/r/pulls" and method == "GET":
            return JSONResponse([PULL] if self.pull_exists else [])
        if path == "/repos/o/r/pulls/7" and method == "PATCH":
            return JSONResponse({**PULL, "title": body["title"]})
        return JSONResponse({"message": "Not Found"}, status_code=404)


@pytest.fixture
async def github() -> AsyncIterator[tuple[FakeGitHubApi, HttpGitHubClient]]:
    api = FakeGitHubApi()
    server = EmbeddedServer(
        Starlette(routes=[Route("/{path:path}", api.handle, methods=["GET", "POST", "PATCH"])])
    )
    await server.start()
    async with httpx.AsyncClient() as http:
        yield api, HttpGitHubClient(http, SecretStr(TOKEN), api_url=server.base_url)
    await server.stop()


async def test_open_pull_request_creates_a_draft_with_the_token(
    github: tuple[FakeGitHubApi, HttpGitHubClient],
) -> None:
    api, client = github
    pr = await client.open_pull_request(
        "o/r", head="sdlc/run_1", base="main", title="Add /health", body="Body"
    )
    assert pr == PullRequest(7, "https://github.com/o/r/pull/7", "PR_7", True)
    method, path, headers, body = api.requests[0]
    assert (method, path) == ("POST", "/repos/o/r/pulls")
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert body == {
        "title": "Add /health",
        "head": "sdlc/run_1",
        "base": "main",
        "body": "Body",
        "draft": True,
    }


async def test_an_existing_pr_for_the_branch_is_updated_instead(
    github: tuple[FakeGitHubApi, HttpGitHubClient],
) -> None:
    api, client = github
    api.pull_exists = True
    pr = await client.open_pull_request(
        "o/r", head="sdlc/run_1", base="main", title="T2", body="B2"
    )
    assert pr.number == 7
    assert [(m, p) for m, p, _, _ in api.requests] == [
        ("POST", "/repos/o/r/pulls"),
        ("GET", "/repos/o/r/pulls"),
        ("PATCH", "/repos/o/r/pulls/7"),
    ]


async def test_mark_ready_uses_graphql_and_reports_errors(
    github: tuple[FakeGitHubApi, HttpGitHubClient],
) -> None:
    api, client = github
    pr = PullRequest(7, "https://github.com/o/r/pull/7", "PR_7", True)
    await client.mark_ready(pr)
    assert api.requests[-1][3]["variables"] == {"id": "PR_7"}
    api.graphql_errors = [{"message": "Resource not accessible by integration"}]
    with pytest.raises(GitHubError, match="not accessible"):
        await client.mark_ready(pr)


async def test_ci_status_reads_check_runs(github: tuple[FakeGitHubApi, HttpGitHubClient]) -> None:
    api, client = github
    assert (await client.ci_status("o/r", "abc")).state == "pending"  # no checks yet
    api.check_runs = [{"name": "python", "status": "completed", "conclusion": "success"}]
    assert await client.ci_status("o/r", "abc") == CiStatus("success", "1 check passed")
    assert api.requests[-1][1] == "/repos/o/r/commits/abc/check-runs"


async def test_errors_raise_without_leaking_the_token(
    github: tuple[FakeGitHubApi, HttpGitHubClient],
) -> None:
    api, client = github
    api.fail_with = 500
    with pytest.raises(GitHubError) as error:
        await client.ci_status("o/r", "abc")
    assert error.value.status == 500 and TOKEN not in str(error.value)


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([], CiStatus("pending", "no checks reported yet")),
        (
            [
                {"name": "a", "status": "in_progress", "conclusion": None},
                {"name": "b", "status": "completed", "conclusion": "success"},
            ],
            CiStatus("pending", "1 of 2 checks still running"),
        ),
        (
            [
                {"name": "web", "status": "completed", "conclusion": "failure"},
                {"name": "a", "status": "in_progress", "conclusion": None},
            ],
            CiStatus("failure", "failed: web"),
        ),
        (
            [{"name": "a", "status": "completed", "conclusion": "skipped"}],
            CiStatus("neutral", "1 check skipped or neutral"),
        ),
    ],
)
def test_summarize_checks(runs: list[dict[str, Any]], expected: CiStatus) -> None:
    assert summarize_checks(runs) == expected
