#!/usr/bin/env python3
"""
Rival Plugin — Azure DevOps Knowledge Exporter

Clones all repos and exports all wikis from an Azure DevOps project
into a local knowledge folder for Rival to index.

Environment variables:
  ADO_PAT           Required. Azure DevOps personal access token.
  ADO_ORG           Required. Azure DevOps organization name (e.g. "rivalitinc").
  ADO_PROJECT       Required. Azure DevOps project name (e.g. "Rival Insurance Technology").
  ADO_OUTPUT_DIR    Optional. Defaults to ./knowledge

Usage:
  ADO_PAT=xxx ADO_ORG=myorg ADO_PROJECT="My Project" python3 export-ado-knowledge.py
  ADO_PAT=xxx ADO_ORG=myorg ADO_PROJECT="My Project" python3 export-ado-knowledge.py --skip-repos
  ADO_PAT=xxx ADO_ORG=myorg ADO_PROJECT="My Project" python3 export-ado-knowledge.py --skip-wikis
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import posixpath
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set
from urllib import error, parse, request


API_VERSION = "7.1"
WIKI_VERSION = "7.1-preview.1"
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
HTML_IMAGE_PATTERN = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
MAX_WIKI_DEPTH = 50


class AzureDevOpsError(RuntimeError):
    pass


def env(name: str, default: Optional[str] = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise AzureDevOpsError(f"Missing required environment variable: {name}")
    return value


def quote_path_component(value: str) -> str:
    return parse.quote(value, safe="")


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._") or "item"


def unique_dest(base_dir: Path, name: str) -> Path:
    """Return a unique directory path, appending _2, _3 etc. on collision."""
    dest = base_dir / name
    if not dest.exists():
        return dest
    counter = 2
    while True:
        candidate = base_dir / f"{name}_{counter}"
        if not candidate.exists():
            return candidate
        counter += 1


def mask_pat(value: str) -> str:
    """Mask a PAT or auth header for safe logging. Show first 4 and last 4 chars only."""
    if len(value) <= 12:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: object) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _sanitize_git_args_for_log(args: Sequence[str]) -> str:
    """Redact auth headers from git args before logging."""
    sanitized = []
    for arg in args:
        if "Authorization:" in arg or "Basic " in arg:
            sanitized.append("[REDACTED_AUTH]")
        else:
            sanitized.append(arg)
    return " ".join(sanitized)


def run_git(args: Sequence[str], cwd: Optional[Path] = None) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        safe_args = _sanitize_git_args_for_log(args)
        # Also redact any auth tokens from stderr/stdout
        safe_stderr = re.sub(r'Basic\s+[A-Za-z0-9+/=]+', 'Basic [REDACTED]', completed.stderr)
        safe_stdout = re.sub(r'Basic\s+[A-Za-z0-9+/=]+', 'Basic [REDACTED]', completed.stdout)
        raise AzureDevOpsError(
            f"git {safe_args} failed with exit code {completed.returncode}\n"
            f"stdout:\n{safe_stdout}\n\nstderr:\n{safe_stderr}"
        )


def log(message: str) -> None:
    """Print progress messages with a Rival prefix."""
    print(f"[rival] {message}", flush=True)


@dataclass
class AzureDevOpsClient:
    organization: str
    project: str
    pat: str

    def __post_init__(self) -> None:
        token = base64.b64encode(f":{self.pat}".encode("utf-8")).decode("ascii")
        self.auth_header = f"Basic {token}"
        self.base_api = f"https://dev.azure.com/{quote_path_component(self.organization)}"

    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        accept: str = "application/json",
        body: Optional[dict] = None,
        dest: Optional[Path] = None,
    ) -> object:
        headers = {
            "Authorization": self.auth_header,
            "Accept": accept,
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")

        req = request.Request(url, headers=headers, method=method, data=data)
        try:
            with request.urlopen(req, timeout=120) as resp:
                payload = resp.read()
                if dest is not None:
                    ensure_parent(dest)
                    dest.write_bytes(payload)
                    return {"path": str(dest), "bytes": len(payload)}
                if accept == "application/json":
                    if not payload:
                        return {}
                    return json.loads(payload.decode("utf-8"))
                return payload
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AzureDevOpsError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise AzureDevOpsError(f"{method} {url} failed: {exc}") from exc
        except socket.timeout as exc:
            raise AzureDevOpsError(f"{method} {url} timed out after 120s") from exc
        except OSError as exc:
            raise AzureDevOpsError(f"{method} {url} network error: {exc}") from exc

    def get_json(self, url: str) -> dict:
        payload = self._request(url, accept="application/json")
        if not isinstance(payload, dict):
            raise AzureDevOpsError(f"Expected JSON object from {url}")
        return payload

    def download(self, url: str, dest: Path) -> None:
        self._request(url, accept="application/octet-stream", dest=dest)

    def test_connection(self) -> str:
        """Validate PAT and project access. Returns empty string on success, error message on failure."""
        # First test: can we reach the org?
        try:
            url = f"{self.base_api}/_apis/projects?api-version={API_VERSION}&$top=1"
            self.get_json(url)
        except AzureDevOpsError as exc:
            return f"Cannot connect to organization '{self.organization}'. Check PAT and org name. ({exc})"
        # Second test: can we access the specific project?
        try:
            url = (
                f"{self.base_api}/{quote_path_component(self.project)}/_apis/git/repositories"
                f"?api-version={API_VERSION}&$top=1"
            )
            self.get_json(url)
        except AzureDevOpsError as exc:
            return f"Connected to org, but project '{self.project}' is not accessible. Check project name and PAT scopes. ({exc})"
        return ""

    def list_repositories(self) -> List[dict]:
        url = (
            f"{self.base_api}/{quote_path_component(self.project)}/_apis/git/repositories"
            f"?api-version={API_VERSION}"
        )
        return self.get_json(url).get("value", [])

    def list_wikis(self) -> List[dict]:
        url = (
            f"{self.base_api}/{quote_path_component(self.project)}/_apis/wiki/wikis"
            f"?api-version={WIKI_VERSION}"
        )
        return self.get_json(url).get("value", [])

    def get_page(
        self,
        wiki_identifier: str,
        path: str,
        *,
        recursion_level: str = "OneLevel",
        include_content: bool = True,
    ) -> dict:
        encoded_path = quote_path_component(path)
        url = (
            f"{self.base_api}/{quote_path_component(self.project)}/_apis/wiki/wikis/"
            f"{quote_path_component(wiki_identifier)}/pages"
            f"?path={encoded_path}"
            f"&recursionLevel={quote_path_component(recursion_level)}"
            f"&includeContent={'true' if include_content else 'false'}"
            f"&api-version={WIKI_VERSION}"
        )
        return self.get_json(url)

    def download_attachment(self, wiki_identifier: str, path: str, dest: Path) -> None:
        encoded_path = quote_path_component(path)
        url = (
            f"{self.base_api}/{quote_path_component(self.project)}/_apis/wiki/wikis/"
            f"{quote_path_component(wiki_identifier)}/attachments"
            f"?path={encoded_path}"
            f"&api-version={WIKI_VERSION}"
        )
        self.download(url, dest)


def clone_or_update_repo(repo_url: str, dest: Path, auth_header: str) -> None:
    extra = f"http.extraheader=Authorization: {auth_header}"
    if dest.exists():
        log(f"  Updating {dest.name}...")
        run_git(["-c", extra, "-C", str(dest), "fetch", "--all", "--prune"])
        try:
            run_git(["-c", extra, "-C", str(dest), "pull", "--ff-only"])
        except AzureDevOpsError:
            pass
        return
    ensure_parent(dest)
    log(f"  Cloning {dest.name}...")
    run_git(["-c", extra, "clone", "--origin", "origin", repo_url, str(dest)])


def strip_query_and_fragment(value: str) -> str:
    parts = parse.urlsplit(value)
    return parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def normalize_wiki_asset_link(link: str) -> Optional[str]:
    link = link.strip()
    if not link or link.startswith("data:") or link.startswith("#"):
        return None
    if link.startswith("http://") or link.startswith("https://"):
        return link
    clean = strip_query_and_fragment(link)
    if clean.startswith("/"):
        return clean
    return clean


def extract_asset_links(markdown: str) -> Set[str]:
    links: Set[str] = set()
    for pattern in (MARKDOWN_IMAGE_PATTERN, HTML_IMAGE_PATTERN):
        for match in pattern.findall(markdown):
            normalized = normalize_wiki_asset_link(match)
            if normalized:
                links.add(normalized)
    return links


def wiki_asset_candidate_paths(page_path: str, link: str) -> Iterable[str]:
    page_dir = posixpath.dirname(page_path) or "/"
    if link.startswith("http://") or link.startswith("https://"):
        return []
    if link.startswith("/"):
        return [link]
    joined = posixpath.normpath(posixpath.join(page_dir, link))
    if not joined.startswith("/"):
        joined = f"/{joined}"
    return [joined]


def local_page_path(export_root: Path, page_path: str) -> Path:
    clean = page_path.strip("/")
    if not clean:
        return export_root / "pages" / "index.md"
    return export_root / "pages" / clean / "index.md"


def export_wiki_tree(client: AzureDevOpsClient, wiki: dict, dest: Path) -> dict:
    wiki_id = wiki["id"]
    exported_pages: List[dict] = []
    downloaded_assets: Dict[str, str] = {}
    visited_paths: Set[str] = set()

    def walk(page: dict, depth: int = 0) -> None:
        if depth > MAX_WIKI_DEPTH:
            log(f"    WARNING: Wiki depth limit ({MAX_WIKI_DEPTH}) reached, skipping deeper pages")
            return

        page_path = page.get("path", "/")

        # Cycle detection
        if page_path in visited_paths:
            return
        visited_paths.add(page_path)

        page_meta = {
            "path": page_path,
            "order": page.get("order"),
            "gitItemPath": page.get("gitItemPath"),
            "isParentPage": page.get("isParentPage"),
            "viewStats": page.get("viewStats"),
        }
        exported_pages.append(page_meta)

        content = page.get("content")
        if isinstance(content, str):
            output_path = local_page_path(dest, page_path)
            ensure_parent(output_path)
            output_path.write_text(content, encoding="utf-8")
            for asset_link in extract_asset_links(content):
                for candidate in wiki_asset_candidate_paths(page_path, asset_link):
                    if candidate in downloaded_assets:
                        break
                    local_asset = dest / "assets" / candidate.strip("/")
                    try:
                        client.download_attachment(wiki_id, candidate, local_asset)
                        downloaded_assets[candidate] = str(local_asset.relative_to(dest))
                        break
                    except AzureDevOpsError:
                        continue

        for child in page.get("subPages", []) or []:
            walk(child, depth + 1)

    root = client.get_page(wiki_id, "/", recursion_level="Full", include_content=True)
    walk(root)

    manifest = {
        "wiki": {
            "id": wiki.get("id"),
            "name": wiki.get("name"),
            "type": wiki.get("type"),
            "mappedPath": wiki.get("mappedPath"),
            "remoteUrl": wiki.get("remoteUrl"),
            "url": wiki.get("url"),
            "repositoryId": (wiki.get("repositoryId") or wiki.get("repository", {}) or {}).get("id")
            if isinstance(wiki.get("repositoryId"), dict)
            else wiki.get("repositoryId"),
            "versions": wiki.get("versions"),
        },
        "pages": exported_pages,
        "assets": downloaded_assets,
    }
    write_json(dest / "manifest.json", manifest)
    return manifest


def repo_clone_url(repo: dict) -> str:
    remote_url = repo.get("remoteUrl") or repo.get("webUrl")
    if not remote_url:
        raise AzureDevOpsError(f"Repository {repo.get('name')} is missing remoteUrl")
    return remote_url


def build_summary(
    repos: Sequence[dict],
    wikis: Sequence[dict],
    repo_root: Path,
    wiki_root: Path,
) -> dict:
    return {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repos": [
            {
                "name": repo.get("name"),
                "id": repo.get("id"),
                "path": str((repo_root / safe_name(repo.get("name", "repo"))).relative_to(repo_root.parent)),
                "remoteUrl": repo.get("remoteUrl"),
                "defaultBranch": repo.get("defaultBranch"),
            }
            for repo in repos
        ],
        "wikis": [
            {
                "name": wiki.get("name"),
                "id": wiki.get("id"),
                "type": wiki.get("type"),
                "path": str((wiki_root / safe_name(wiki.get("name", "wiki"))).relative_to(wiki_root.parent)),
                "remoteUrl": wiki.get("remoteUrl"),
                "mappedPath": wiki.get("mappedPath"),
            }
            for wiki in wikis
        ],
    }


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Rival Plugin — Export Azure DevOps repos and wikis into a knowledge folder"
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("ADO_OUTPUT_DIR", "knowledge"),
        help="Target directory for exported content (default: ./knowledge)",
    )
    parser.add_argument("--skip-repos", action="store_true", help="Skip repository cloning")
    parser.add_argument("--skip-wikis", action="store_true", help="Skip wiki export")
    parser.add_argument("--test-connection", action="store_true", help="Only test the PAT connection, then exit")
    args = parser.parse_args(argv)

    log("Connecting to Azure DevOps...")
    client = AzureDevOpsClient(
        organization=env("ADO_ORG"),
        project=env("ADO_PROJECT"),
        pat=env("ADO_PAT"),
    )

    if args.test_connection:
        err = client.test_connection()
        if not err:
            log("Connection successful! Organization and project verified.")
            return 0
        else:
            log(f"Connection FAILED: {err}")
            return 1

    err = client.test_connection()
    if err:
        log(f"ERROR: {err}")
        return 1
    log("Connection verified.")

    output_dir = Path(args.output_dir).resolve()
    repo_root = output_dir / "repos"
    wiki_root = output_dir / "wikis"
    output_dir.mkdir(parents=True, exist_ok=True)

    repos: List[dict] = []
    wikis: List[dict] = []

    if not args.skip_repos:
        log("Fetching repository list...")
        try:
            repos = client.list_repositories()
        except AzureDevOpsError as exc:
            log(f"ERROR: Failed to list repositories. Check PAT has Code (Read) scope.\n  {exc}")
            return 1
        log(f"Found {len(repos)} repositories. Cloning...")
        used_names: Dict[str, str] = {}  # safe_name -> original name, for collision detection
        for i, repo in enumerate(repos, 1):
            original_name = repo.get("name", "repo")
            name = safe_name(original_name)
            if name in used_names and used_names[name] != original_name:
                log(f"    NOTE: '{original_name}' collides with '{used_names[name]}' after sanitization")
            used_names[name] = original_name
            dest = unique_dest(repo_root, name)
            log(f"  [{i}/{len(repos)}] {original_name}")
            try:
                clone_or_update_repo(repo_clone_url(repo), dest, client.auth_header)
            except AzureDevOpsError as exc:
                log(f"  WARNING: Failed to clone {original_name}: {exc}")
        log(f"Repos done: {len(repos)} processed.")

    if not args.skip_wikis:
        log("Fetching wiki list...")
        try:
            wikis = client.list_wikis()
        except AzureDevOpsError as exc:
            log(
                f"WARNING: Wiki export failed. The PAT may need Wiki Read scope.\n"
                f"  Error: {exc}\n"
                f"  Continuing without wikis..."
            )
            wikis = []

        if wikis:
            log(f"Found {len(wikis)} wiki(s). Exporting...")
            for wiki in wikis:
                name = safe_name(wiki.get("name", "wiki"))
                wiki_dest = wiki_root / name
                wiki_dest.mkdir(parents=True, exist_ok=True)
                log(f"  Exporting wiki: {name}")
                try:
                    export_wiki_tree(client, wiki, wiki_dest)
                except AzureDevOpsError as exc:
                    log(f"  WARNING: Failed to export wiki {name}: {exc}")
                except (RecursionError, KeyError, TypeError) as exc:
                    log(f"  WARNING: Wiki {name} has malformed structure, skipping: {type(exc).__name__}: {exc}")

                remote_url = wiki.get("remoteUrl")
                if remote_url:
                    git_dest = wiki_dest / "git"
                    try:
                        clone_or_update_repo(remote_url, git_dest, client.auth_header)
                    except AzureDevOpsError:
                        pass
            log(f"Wikis done: {len(wikis)} processed.")

    summary = build_summary(repos, wikis, repo_root, wiki_root)
    write_json(output_dir / "summary.json", summary)

    log("=" * 50)
    log(f"Export complete!")
    log(f"  Repos:  {len(repos)}")
    log(f"  Wikis:  {len(wikis)}")
    log(f"  Output: {output_dir}")
    log("=" * 50)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AzureDevOpsError as exc:
        print(f"[rival] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
