"""Submit through the public PowderBench contract: a PR from our fork that
touches only data/submissions/<league>/<round>/<team>.csv.

Same mechanics as `powderbench submit` (fork, branch, contents API, PR) but
safe for repeated updates: an open PR of ours is updated in place; once a
PR has merged, an update goes on a *fresh* branch cut from upstream main,
so it is a clean one-file modification the auto-merge bot accepts (reusing
the old branch would conflict with the squash-merged file).

Deadline guard: nothing is pushed within GUARD_MIN of a cutoff. The rules
judge timeliness by the merge on main (<= cutoff + 5 min) and auto-merge
needs a few minutes; we also never touch a round after its cutoff.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import TEAM, UPSTREAM
from .contract import Round

API = "https://api.github.com"
GUARD_MIN = 45


class TooLate(RuntimeError):
    pass


def _s() -> requests.Session:
    tok = os.environ.get("POWDERBENCH_TOKEN") or os.environ.get("GH_TOKEN")
    if not tok:
        raise RuntimeError("set POWDERBENCH_TOKEN (classic PAT, public_repo scope, of the team's account)")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28"})
    return s


def _req(s, method: str, path: str, ok=(200, 201), **kw) -> requests.Response:
    for attempt in range(4):
        r = s.request(method, API + path, timeout=60, **kw)
        if r.status_code in ok or r.status_code in (404, 409, 422):
            return r
        time.sleep(5 * (attempt + 1))
    r.raise_for_status()
    return r


def sub_path(r: Round, team: str = TEAM) -> str:
    return f"data/submissions/{r.league}/{r.round_date.isoformat()}/{team}.csv"


def upstream_file(r: Round, team: str = TEAM) -> str | None:
    """Our CSV as merged on upstream main (None if not there). The contents
    API is uncached, unlike raw.githubusercontent.com."""
    try:
        s = _s()
        s.headers["Accept"] = "application/vnd.github.raw+json"
        resp = s.get(f"{API}/repos/{UPSTREAM}/contents/{sub_path(r, team)}", params={"ref": "main"}, timeout=60)
    except RuntimeError:  # no token: fall back to the (CDN-cached) raw URL
        resp = requests.get(f"https://raw.githubusercontent.com/{UPSTREAM}/main/{sub_path(r, team)}", timeout=60)
    return resp.text if resp.status_code == 200 else None


@dataclass
class Submitted:
    pr_url: str
    branch: str
    updated_existing_pr: bool


def check_deadline(r: Round, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if r.hours_left(now) * 60 < GUARD_MIN:
        raise TooLate(f"{r.key}: {r.hours_left(now) * 60:.0f} min to cutoff (< {GUARD_MIN}) — not submitting")


def submit(r: Round, csv_path: Path, note: str = "", team: str = TEAM) -> Submitted:
    check_deadline(r)
    s = _s()
    login = _req(s, "GET", "/user").json()["login"]
    owner, name = UPSTREAM.split("/")
    fork = UPSTREAM if login == owner else f"{login}/{name}"
    if fork != UPSTREAM and _req(s, "GET", f"/repos/{fork}").status_code == 404:
        _req(s, "POST", f"/repos/{UPSTREAM}/forks", ok=(202,), json={"default_branch_only": True})
        for _ in range(30):  # forks are created asynchronously
            time.sleep(5)
            if _req(s, "GET", f"/repos/{fork}").status_code == 200:
                break
    path = sub_path(r, team)
    content = base64.b64encode(csv_path.read_bytes()).decode()
    title = f"Submission: {team} ({r.league}/{r.round_date})"
    body = (f"Forecast for round `{r.league}/{r.round_date}` (cutoff {r.cutoff:%Y-%m-%d %H:%M} UTC) by "
            f"[open-powder-ml](https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'open-powder-ml')}), "
            f"an open-source ML entry, submitted automatically.\n\n{note}")

    # an open PR of ours for this round: update its branch in place
    prs = _req(s, "GET", f"/repos/{UPSTREAM}/pulls", params={"state": "open", "per_page": 100}).json()
    mine = [p for p in prs if p["user"]["login"] == login and p["title"] == title]
    if mine:
        br = mine[0]["head"]["ref"]
        _put(s, fork, path, br, content, title)
        _req(s, "PATCH", f"/repos/{UPSTREAM}/pulls/{mine[0]['number']}", json={"body": body})
        return Submitted(mine[0]["html_url"], br, True)

    main_sha = _req(s, "GET", f"/repos/{UPSTREAM}/git/ref/heads/main").json()["object"]["sha"]
    br = f"submit-{team}-{r.league}-{r.round_date}-{datetime.now(timezone.utc):%H%M%S}"
    resp = _req(s, "POST", f"/repos/{fork}/git/refs", json={"ref": f"refs/heads/{br}", "sha": main_sha})
    if resp.status_code == 422 and fork != UPSTREAM:  # fork lacks the commit: sync, retry
        _req(s, "POST", f"/repos/{fork}/merge-upstream", json={"branch": "main"})
        resp = _req(s, "POST", f"/repos/{fork}/git/refs", json={"ref": f"refs/heads/{br}", "sha": main_sha})
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"could not create branch {br}: {resp.status_code} {resp.text[:200]}")
    _put(s, fork, path, br, content, title)
    head = br if fork == UPSTREAM else f"{login}:{br}"
    check_deadline(r)
    pr = _req(s, "POST", f"/repos/{UPSTREAM}/pulls",
              json={"title": title, "head": head, "base": "main", "body": body})
    if pr.status_code not in (200, 201):
        raise RuntimeError(f"PR create failed: {pr.status_code} {pr.text[:300]}")
    return Submitted(pr.json()["html_url"], br, False)


def _put(s, repo: str, path: str, branch: str, content: str, title: str) -> None:
    cur = _req(s, "GET", f"/repos/{repo}/contents/{path}", params={"ref": branch})
    payload = {"message": title, "content": content, "branch": branch}
    if cur.status_code == 200:
        payload["sha"] = cur.json()["sha"]
    resp = _req(s, "PUT", f"/repos/{repo}/contents/{path}", json=payload)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"upload failed: {resp.status_code} {resp.text[:300]}")


def wait_merged(r: Round, csv_text: str, timeout_s: int = 900, team: str = TEAM) -> bool:
    """Poll upstream main until our file there matches what we sent."""
    end = time.time() + timeout_s
    while time.time() < end:
        if (upstream_file(r, team) or "").strip() == csv_text.strip():
            return True
        time.sleep(30)
    return False
