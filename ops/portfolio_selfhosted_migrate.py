#!/usr/bin/env python3
"""Convert hosted GitHub Actions job selectors to repository-scoped VPS runners.

Owner-only operations workflow invokes this on an existing self-hosted runner.
Uses an already authorized GitHub CLI credential without printing it. Creates
one atomic skip-ci commit per repository; never force-pushes a changed ref.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

OWNER = "Zennay"
# Each label set is confirmed against a registered, repository-scoped runner.
RUNNERS = {
    "zCloud": "[self-hosted, zcloud, vps]",
    "Haxlab": "[self-hosted, haxlab]",
    "Supa": "[self-hosted, supa, vps]",
    "RaiseAI": "[self-hosted, vps-bb300bba]",
    "Lightup": "[self-hosted, lightup, vps]",
    "zSSH": "[self-hosted, zssh, vps]",
    "Ftmo": "[self-hosted, ftmo-research]",
    "Discordbot": "[self-hosted, Linux]",
    "zGuard": "[self-hosted, portfolio-pool, vps]",
    "Woonpoints": "[self-hosted, portfolio-pool, vps]",
    "Mixlab": "[self-hosted, portfolio-pool, vps]",
    "ikea-enhanced-app": "[self-hosted, portfolio-pool, vps]",
    "fuckball": "[self-hosted, portfolio-pool, vps]",
    "Tuddy": "[self-hosted, portfolio-pool, vps]",
}
PR_GUARD = ("((github.event_name != 'pull_request' && "
            "github.event_name != 'pull_request_target') || "
            "(github.event.pull_request.head.repo.full_name == github.repository "
            "&& github.actor == 'Zennay'))")
JOB = re.compile(r"^  [A-Za-z0-9_.-]+:[ \t]*(?:#.*)?$")
HOSTED = re.compile(r"^ubuntu-(?:latest|[0-9]{2}\.[0-9]{2})(?:-[a-z0-9-]+)?$")


def migrate_yaml(source: str, selector: str) -> tuple[str, int, list[str]]:
    lines = source.splitlines(keepends=True)
    start = next((i for i, x in enumerate(lines) if re.fullmatch(r"jobs:\s*", x.strip())), None)
    if start is None:
        return source, 0, ["no_jobs_mapping"]
    boundaries = [i for i in range(start + 1, len(lines)) if JOB.match(lines[i])]
    if not boundaries:
        return source, 0, ["no_job_definitions"]
    boundaries.append(len(lines))
    replacements: list[tuple[int, int, list[str]]] = []
    problems = []
    jobs_moved = 0
    for lo, hi in zip(boundaries, boundaries[1:]):
        name = lines[lo].strip().split(":")[0]
        found = [(i, re.match(r"^    runs-on:\s*(.*?)\s*(?:#.*)?$", lines[i].rstrip("\n")))
                 for i in range(lo + 1, hi)]
        found = [(i, m) for i, m in found if m]
        for idx, match in found:
            value = match.group(1).strip()
            if not HOSTED.fullmatch(value):
                if any(x in value for x in ("ubuntu", "windows", "macos")):
                    problems.append(name + ":unsupported_selector:" + value[:60])
                continue
            # Some workflows explicitly enforce github-hosted environment; refuse an
            # unsafe mechanical migration rather than weaken admission semantics.
            job_text = "".join(lines[lo:hi])
            if "RUNNER_ENVIRONMENT" in job_text and "github-hosted" in job_text:
                problems.append(name + ":github_hosted_environment_contract")
                continue
            existing_if = next((p for p in range(lo + 1, hi)
                                if re.match(r"^    if:\s*", lines[p])), None)
            guard_insert = None
            if existing_if is None:
                guard_insert = (idx, idx, ["    if: " + PR_GUARD + "\n"])
            else:
                old_if = lines[existing_if].rstrip("\n")
                raw = old_if.split("if:", 1)[1].strip()
                if raw in (">-", ">", "|", "|-"):
                    end = existing_if + 1
                    while end < hi and (
                        lines[end].strip() == "" or
                        len(lines[end]) - len(lines[end].lstrip()) >= 6
                    ):
                        end += 1
                    if end == existing_if + 1 or "${{" in "".join(lines[existing_if:end]):
                        problems.append(name + ":complex_if_gate")
                        continue
                    guard_insert = (end, end, ["      && " + PR_GUARD + "\n"])
                elif raw.startswith("${{") and raw.endswith("}}"):
                    inner = raw[3:-2].strip()
                    guard_insert = (existing_if, existing_if + 1,
                                    ["    if: ${{ (" + inner + ") && " + PR_GUARD + " }}\n"])
                elif raw and not raw.startswith(('"', "'")):
                    guard_insert = (existing_if, existing_if + 1,
                                    ["    if: (" + raw + ") && " + PR_GUARD + "\n"])
                else:
                    problems.append(name + ":unsupported_if_gate")
                    continue
            replacements.append((idx, idx + 1, ["    runs-on: " + selector + "\n"]))
            replacements.append(guard_insert)
            jobs_moved += 1
    for lo, hi, rep in sorted(replacements, key=lambda t: (t[0], t[1]), reverse=True):
        lines[lo:hi] = rep
    result = "".join(lines)
    if jobs_moved and result.count("    runs-on: " + selector) < jobs_moved:
        raise ValueError("runner selector substitution invariant failed")
    return result, jobs_moved, problems


def request(token: str, method: str, endpoint: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    url = "https://api.github.com/" + endpoint.lstrip("/")
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "zennay-selfhosted-portfolio-migration",
        })
    try:
        with urllib.request.urlopen(req, timeout=25) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        payload = exc.read(600).decode("utf-8", "replace")
        raise RuntimeError(f"GitHub API {method} {endpoint}: {exc.code}: {payload}") from None


def ensure_runner(token: str, name: str):
    candidates = request(token, "GET", f"repos/{OWNER}/{name}/actions/runners?per_page=100")
    registered = candidates.get("runners", [])
    selector = RUNNERS[name].strip("[]").split(", ")
    for runner in registered:
        labels = {x["name"] for x in runner["labels"]}
        if not set(selector).issubset(labels):
            continue
        if runner["status"] == "online":
            return runner["name"]
        # Registered but parked pool listeners have a verified unique identity.
        # They must be installed without starting untrusted pre-migration code.
        if name in {"zGuard", "Woonpoints", "Mixlab", "ikea-enhanced-app", "fuckball", "Tuddy"} and runner["name"] == "vps-bb300bba-pool-" + name:
            return runner["name"]
    raise ValueError("no matching ONLINE self-hosted runner")


def run_repo(token: str, name: str):
    runner_name = ensure_runner(token, name)
    repo = request(token, "GET", f"repos/{OWNER}/{name}")
    if repo.get("archived"):
        return {"repo": name, "status": "archived"}
    branch = repo["default_branch"]
    ref_name = urllib.parse.quote(branch, safe="/")
    ref = request(token, "GET", f"repos/{OWNER}/{name}/git/ref/heads/{ref_name}")
    parent_sha = ref["object"]["sha"]
    commit = request(token, "GET", f"repos/{OWNER}/{name}/git/commits/{parent_sha}")
    tree_sha = commit["tree"]["sha"]
    tree = request(token, "GET", f"repos/{OWNER}/{name}/git/trees/{tree_sha}?recursive=1")
    if tree.get("truncated"):
        raise ValueError("GitHub tree truncated; refusing partial migration")
    workflows = [
        entry for entry in tree["tree"]
        if entry["type"] == "blob"
        and re.fullmatch(r"\.github/workflows/[^/]+\.ya?ml", entry["path"])
    ]
    changes = []
    report = []
    for file in workflows:
        blob = request(token, "GET", f"repos/{OWNER}/{name}/git/blobs/{file['sha']}")
        raw = base64.b64decode(blob["content"]).decode("utf-8")
        patched, moved, issues = migrate_yaml(raw, RUNNERS[name])
        if issues:
            report.append((file["path"], issues))
        if moved and raw != patched:
            changes.append({
                "path": file["path"], "mode": file.get("mode", "100644"),
                "type": "blob", "content": patched,
            })
    if not changes:
        return {"repo": name, "runner": runner_name, "status": "unchanged",
                "workflow_count": len(workflows), "skipped": report[:20]}
    new_tree = request(token, "POST", f"repos/{OWNER}/{name}/git/trees",
                       {"base_tree": tree_sha, "tree": changes})
    updated = request(token, "POST", f"repos/{OWNER}/{name}/git/commits", {
        "message": "ci: enforce VPS-only self-hosted GitHub Actions runners [skip ci]",
        "tree": new_tree["sha"],
        "parents": [parent_sha],
    })
    new_sha = updated["sha"]
    try:
        request(token, "PATCH", f"repos/{OWNER}/{name}/git/refs/heads/{ref_name}",
                {"sha": new_sha, "force": False})
        outcome = "committed"
    except RuntimeError as error:
        # Ref never moves backward or with force. When protected, preserve work
        # on a dedicated branch for a separately reviewed pull request.
        runid = str(__import__("os").environ.get("GITHUB_RUN_ID", "manual"))
        new_branch = "ops/selfhosted-runner-migration-" + runid
        request(token, "POST", f"repos/{OWNER}/{name}/git/refs", {
            "ref": "refs/heads/" + new_branch, "sha": new_sha})
        request(token, "POST", f"repos/{OWNER}/{name}/pulls", {
            "title": "ci: replace GitHub-hosted jobs with VPS self-hosted runners",
            "head": new_branch, "base": branch,
            "body": "VPS-only runner routing, with owner/same-repository PR admission. This branch exists because main rejected an atomic fast-forward update. No QA suite dispatched.",
        })
        outcome = "pull_request"
    return {"repo": name, "runner": runner_name, "status": outcome,
            "commit": new_sha, "workflow_count": len(workflows),
            "files_changed": len(changes), "skipped": report[:30]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True, help="Comma-separated owned repo names")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    names = [x.strip() for x in args.repos.split(",") if x.strip()]
    if not args.apply:
        raise SystemExit("Migration requires explicit --apply on owner-only self-hosted workflow")
    if not names or len(names) != len(set(names)) or any(x not in RUNNERS for x in names):
        raise SystemExit("Unknown or repeated repo in migration scope")
    token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    if len(token) < 20:
        raise SystemExit("Missing authenticated GitHub CLI token")
    results = []
    for name in names:
        try:
            outcome = run_repo(token, name)
        except Exception as e:
            outcome = {"repo": name, "status": "error", "reason": str(e)[:350]}
        results.append(outcome)
        print(json.dumps(outcome, sort_keys=True), flush=True)
    if any(x["status"] == "error" for x in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
