"""GitHub calls the orchestrator makes with the run's token (agents never see it)."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from pydantic import SecretStr

CiState = Literal["pending", "success", "failure", "neutral"]
_FAILED = {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}
_NEUTRAL = {"neutral", "skipped"}
_MARK_READY = """
mutation($id: ID!) {
  markPullRequestReadyForReview(input: {pullRequestId: $id}) { pullRequest { isDraft } }
}
"""


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    node_id: str
    draft: bool


@dataclass(frozen=True)
class CiStatus:
    state: CiState
    summary: str


def summarize_checks(check_runs: list[dict[str, Any]]) -> CiStatus:
    """Zero check runs means CI hasn't reported yet, not that it passed."""
    if not check_runs:
        return CiStatus("pending", "no checks reported yet")
    failed = sorted(str(r.get("name")) for r in check_runs if r.get("conclusion") in _FAILED)
    if failed:
        return CiStatus("failure", "failed: " + ", ".join(failed))
    running = [r for r in check_runs if r.get("status") != "completed"]
    if running:
        return CiStatus("pending", f"{len(running)} of {len(check_runs)} checks still running")
    if all(r.get("conclusion") in _NEUTRAL for r in check_runs):
        noun = "check" if len(check_runs) == 1 else "checks"
        return CiStatus("neutral", f"{len(check_runs)} {noun} skipped or neutral")
    noun = "check" if len(check_runs) == 1 else "checks"
    return CiStatus("success", f"{len(check_runs)} {noun} passed")


class GitHubClient(Protocol):
    async def open_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequest: ...

    async def update_pull_request(
        self, repo: str, number: int, *, title: str, body: str
    ) -> PullRequest: ...

    async def mark_ready(self, pr: PullRequest) -> None: ...

    async def ci_status(self, repo: str, sha: str) -> CiStatus: ...


def _pull(data: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=int(data["number"]),
        url=str(data["html_url"]),
        node_id=str(data["node_id"]),
        draft=bool(data.get("draft")),
    )


class HttpGitHubClient:
    def __init__(
        self, http: httpx.AsyncClient, token: SecretStr, api_url: str = "https://api.github.com"
    ) -> None:
        self._http = http
        self._token = token
        self._api = api_url.rstrip("/")

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: dict[str, str] | None = None
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agentic-sdlc",
        }
        try:
            response = await self._http.request(
                method,
                f"{self._api}{path}",
                json=json,
                params=params,
                headers=headers,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub {method} {path}: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            detail = response.text[:300].replace(self._token.get_secret_value(), "***")
            raise GitHubError(
                f"GitHub {method} {path}: HTTP {response.status_code} {detail}",
                status=response.status_code,
            )
        return response.json() if response.content else None

    async def open_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequest:
        payload = {"title": title, "head": head, "base": base, "body": body, "draft": True}
        try:
            return _pull(await self._request("POST", f"/repos/{repo}/pulls", json=payload))
        except GitHubError as exc:
            if exc.status != 422:
                raise
            # A PR for this branch already exists (a resumed run created it before a restart).
            owner = repo.split("/", 1)[0]
            existing = await self._request(
                "GET", f"/repos/{repo}/pulls", params={"head": f"{owner}:{head}", "state": "open"}
            )
            if not existing:
                raise
            number = int(existing[0]["number"])
            return await self.update_pull_request(repo, number, title=title, body=body)

    async def update_pull_request(
        self, repo: str, number: int, *, title: str, body: str
    ) -> PullRequest:
        data = await self._request(
            "PATCH", f"/repos/{repo}/pulls/{number}", json={"title": title, "body": body}
        )
        return _pull(data)

    async def mark_ready(self, pr: PullRequest) -> None:
        if not pr.draft:
            return
        data = await self._request(
            "POST", "/graphql", json={"query": _MARK_READY, "variables": {"id": pr.node_id}}
        )
        errors = (data or {}).get("errors")
        if errors:
            raise GitHubError(
                "marking the PR ready failed: " + "; ".join(e.get("message", "?") for e in errors)
            )

    async def ci_status(self, repo: str, sha: str) -> CiStatus:
        data = await self._request(
            "GET", f"/repos/{repo}/commits/{sha}/check-runs", params={"per_page": "100"}
        )
        return summarize_checks(list((data or {}).get("check_runs", [])))
