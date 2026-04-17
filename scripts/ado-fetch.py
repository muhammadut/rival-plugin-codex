#!/usr/bin/env python3
"""
Rival Plugin — Azure DevOps Single-Item Fetcher

Fetches a single PR or work item with full details for investigation.
Outputs structured JSON to stdout.

Environment variables (from .env):
  ADO_PAT, ADO_ORG, ADO_PROJECT

Usage:
  python3 ado-fetch.py --pr 7337
  python3 ado-fetch.py --pr 7337 --with-comments --with-diff
  python3 ado-fetch.py --ticket 26961
  python3 ado-fetch.py --ticket 26961 --with-history
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from urllib import error, parse, request


API_VERSION = "7.1"


class AzureDevOpsError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"[rival] {msg}", file=sys.stderr, flush=True)


def quote(value: str) -> str:
    return parse.quote(value, safe="")


def load_env(env_path: Path) -> Dict[str, str]:
    env = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key.startswith("ADO_"):
            env[key] = value
    return env


class Client:
    def __init__(self, org: str, project: str, pat: str):
        token = base64.b64encode(f":{pat}".encode()).decode()
        self.auth = f"Basic {token}"
        self.base = f"https://dev.azure.com/{quote(org)}"
        self.proj = f"{self.base}/{quote(project)}"

    def get(self, url: str) -> dict:
        req = request.Request(url, headers={
            "Authorization": self.auth,
            "Accept": "application/json",
        })
        try:
            with request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                return json.loads(data.decode("utf-8")) if data else {}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise AzureDevOpsError(f"HTTP {exc.code}: {detail}") from exc
        except (error.URLError, socket.timeout, OSError) as exc:
            raise AzureDevOpsError(f"Network error: {exc}") from exc

    # ---- Pull Request ----

    def fetch_pr(self, pr_id: int, with_comments: bool = False, with_diff: bool = False) -> Dict:
        log(f"Fetching PR #{pr_id}...")

        # Find which repo has this PR (search project-wide)
        url = f"{self.proj}/_apis/git/pullrequests/{pr_id}?api-version={API_VERSION}"
        pr = self.get(url)

        repo_id = pr.get("repository", {}).get("id", "")
        repo_name = pr.get("repository", {}).get("name", "")

        result = {
            "type": "pull_request",
            "id": pr_id,
            "repo": repo_name,
            "title": pr.get("title", ""),
            "description": pr.get("description", ""),
            "status": pr.get("status", ""),
            "source_branch": pr.get("sourceRefName", "").replace("refs/heads/", ""),
            "target_branch": pr.get("targetRefName", "").replace("refs/heads/", ""),
            "created_by": pr.get("createdBy", {}).get("displayName", ""),
            "created_date": pr.get("creationDate", ""),
            "reviewers": [
                {
                    "name": r.get("displayName", ""),
                    "vote": r.get("vote", 0),
                    "vote_label": {0: "No vote", 5: "Approved with suggestions",
                                   10: "Approved", -5: "Waiting", -10: "Rejected"}.get(r.get("vote", 0), "Unknown"),
                }
                for r in pr.get("reviewers", [])
            ],
            "merge_status": pr.get("mergeStatus", ""),
            "labels": [l.get("name", "") for l in pr.get("labels", [])],
        }

        # Commits
        log(f"  Fetching commits...")
        commits_url = f"{self.base}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}/commits?api-version={API_VERSION}&$top=50"
        commits_data = self.get(commits_url)
        result["commits"] = [
            {
                "id": c.get("commitId", "")[:8],
                "message": c.get("comment", "").split("\n")[0][:200],
                "author": c.get("author", {}).get("name", ""),
                "date": c.get("author", {}).get("date", ""),
            }
            for c in commits_data.get("value", [])
        ]

        # Changed files (from latest iteration)
        log(f"  Fetching changed files...")
        try:
            iter_url = f"{self.base}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}/iterations?api-version={API_VERSION}"
            iters = self.get(iter_url).get("value", [])
            if iters:
                last_iter = iters[-1]["id"]
                changes_url = f"{self.base}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}/iterations/{last_iter}/changes?api-version={API_VERSION}&$top=200"
                changes = self.get(changes_url).get("changeEntries", [])
                result["files_changed"] = [
                    {
                        "path": c.get("item", {}).get("path", ""),
                        "change_type": c.get("changeType", ""),
                    }
                    for c in changes
                ]
                result["files_count"] = len(changes)
        except AzureDevOpsError:
            result["files_changed"] = []
            result["files_count"] = 0

        # Comments / threads
        if with_comments:
            log(f"  Fetching review comments...")
            try:
                threads_url = f"{self.base}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}/threads?api-version={API_VERSION}"
                threads = self.get(threads_url).get("value", [])
                result["threads"] = [
                    {
                        "status": t.get("status", ""),
                        "file": (t.get("threadContext") or {}).get("filePath", ""),
                        "comments": [
                            {
                                "author": c.get("author", {}).get("displayName", ""),
                                "content": c.get("content", "")[:500],
                                "date": c.get("publishedDate", ""),
                            }
                            for c in t.get("comments", [])
                            if c.get("commentType") != "system"
                        ],
                    }
                    for t in threads
                    if any(c.get("commentType") != "system" for c in t.get("comments", []))
                ]
            except AzureDevOpsError:
                result["threads"] = []

        # Diff
        if with_diff:
            log(f"  Fetching diff...")
            try:
                source = pr.get("sourceRefName", "")
                target = pr.get("targetRefName", "")
                diff_url = (
                    f"{self.base}/_apis/git/repositories/{repo_id}/diffs/commits"
                    f"?baseVersion={quote(target.replace('refs/heads/', ''))}"
                    f"&baseVersionType=branch"
                    f"&targetVersion={quote(source.replace('refs/heads/', ''))}"
                    f"&targetVersionType=branch"
                    f"&api-version={API_VERSION}"
                )
                diff_data = self.get(diff_url)
                result["diff_summary"] = {
                    "adds": diff_data.get("aheadCount", 0),
                    "behind": diff_data.get("behindCount", 0),
                    "changes": len(diff_data.get("changes", [])),
                }
            except AzureDevOpsError:
                result["diff_summary"] = {}

        log(f"  Done. {len(result.get('commits', []))} commits, {result.get('files_count', 0)} files changed.")
        return result

    # ---- Work Item ----

    def fetch_ticket(self, ticket_id: int, with_history: bool = False) -> Dict:
        log(f"Fetching ticket #{ticket_id}...")

        url = f"{self.proj}/_apis/wit/workitems/{ticket_id}?$expand=relations&api-version={API_VERSION}"
        wi = self.get(url)
        fields = wi.get("fields", {})

        # Strip HTML from description
        desc = fields.get("System.Description", "") or ""
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"&nbsp;", " ", desc)
        desc = re.sub(r"&amp;", "&", desc)
        desc = re.sub(r"\s+", " ", desc).strip()

        acceptance = fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or ""
        acceptance = re.sub(r"<[^>]+>", " ", acceptance)
        acceptance = re.sub(r"\s+", " ", acceptance).strip()

        result = {
            "type": "work_item",
            "id": ticket_id,
            "title": fields.get("System.Title", ""),
            "work_item_type": fields.get("System.WorkItemType", ""),
            "state": fields.get("System.State", ""),
            "assigned_to": (fields.get("System.AssignedTo") or {}).get("displayName", "Unassigned")
                          if isinstance(fields.get("System.AssignedTo"), dict) else str(fields.get("System.AssignedTo", "Unassigned")),
            "area_path": fields.get("System.AreaPath", ""),
            "iteration_path": fields.get("System.IterationPath", ""),
            "priority": fields.get("Microsoft.VSTS.Common.Priority", ""),
            "severity": fields.get("Microsoft.VSTS.Common.Severity", ""),
            "description": desc[:3000],
            "acceptance_criteria": acceptance[:2000],
            "tags": fields.get("System.Tags", ""),
            "created_date": fields.get("System.CreatedDate", ""),
            "changed_date": fields.get("System.ChangedDate", ""),
            "created_by": (fields.get("System.CreatedBy") or {}).get("displayName", "")
                          if isinstance(fields.get("System.CreatedBy"), dict) else "",
        }

        # Related items
        relations = wi.get("relations", []) or []
        result["related_items"] = []
        for rel in relations:
            rel_type = rel.get("attributes", {}).get("name", rel.get("rel", ""))
            url_str = rel.get("url", "")
            # Extract work item ID from URL
            wi_id_match = re.search(r"/workItems/(\d+)$", url_str)
            if wi_id_match:
                result["related_items"].append({
                    "id": int(wi_id_match.group(1)),
                    "relation": rel_type,
                })

        # Comments
        log(f"  Fetching comments...")
        try:
            comments_url = f"{self.proj}/_apis/wit/workItems/{ticket_id}/comments?api-version={API_VERSION}-preview.4"
            comments_data = self.get(comments_url)
            result["comments"] = [
                {
                    "author": c.get("createdBy", {}).get("displayName", ""),
                    "text": re.sub(r"<[^>]+>", " ", c.get("text", ""))[:500].strip(),
                    "date": c.get("createdDate", ""),
                }
                for c in comments_data.get("comments", [])
            ]
        except AzureDevOpsError:
            result["comments"] = []

        # History (state changes)
        if with_history:
            log(f"  Fetching history...")
            try:
                history_url = f"{self.proj}/_apis/wit/workItems/{ticket_id}/updates?api-version={API_VERSION}"
                updates = self.get(history_url).get("value", [])
                state_changes = []
                for u in updates:
                    state_field = (u.get("fields") or {}).get("System.State", {})
                    if state_field and "newValue" in state_field:
                        state_changes.append({
                            "from": state_field.get("oldValue", ""),
                            "to": state_field.get("newValue", ""),
                            "by": (u.get("revisedBy") or {}).get("displayName", ""),
                            "date": u.get("revisedDate", ""),
                        })
                result["state_history"] = state_changes
            except AzureDevOpsError:
                result["state_history"] = []

        log(f"  Done. {len(result.get('related_items', []))} related, {len(result.get('comments', []))} comments.")
        return result


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Rival — Fetch ADO PR or ticket details")
    parser.add_argument("--pr", type=int, help="Pull request ID")
    parser.add_argument("--ticket", type=int, help="Work item ID")
    parser.add_argument("--with-comments", action="store_true", help="Include review comments/threads")
    parser.add_argument("--with-diff", action="store_true", help="Include diff summary (PRs only)")
    parser.add_argument("--with-history", action="store_true", help="Include state change history (tickets only)")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    args = parser.parse_args(argv)

    if not args.pr and not args.ticket:
        parser.error("Provide --pr <id> or --ticket <id>")

    env = load_env(Path(args.env))
    pat = env.get("ADO_PAT") or os.environ.get("ADO_PAT", "")
    org = env.get("ADO_ORG") or os.environ.get("ADO_ORG", "")
    project = env.get("ADO_PROJECT") or os.environ.get("ADO_PROJECT", "")

    if not all([pat, org, project]):
        log(f"ERROR: Missing ADO_PAT, ADO_ORG, or ADO_PROJECT")
        return 1

    client = Client(org, project, pat)

    if args.pr:
        result = client.fetch_pr(args.pr, with_comments=args.with_comments, with_diff=args.with_diff)
    else:
        result = client.fetch_ticket(args.ticket, with_history=args.with_history)

    # Output JSON to stdout (logs go to stderr)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AzureDevOpsError as exc:
        log(f"ERROR: {exc}")
        raise SystemExit(1)
