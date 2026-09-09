"""The narrow Beads interface used by Asco."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterable


class BeadsError(RuntimeError):
    """A Beads command failed."""


class Beads:
    def __init__(self, root: Path):
        self.root = root

    def run(self, *args: str, json_output: bool = False) -> Any:
        command = ["bd", *args]
        if json_output:
            command.append("--json")
        result = subprocess.run(command, cwd=self.root, text=True, capture_output=True)
        if result.returncode:
            raise BeadsError(result.stderr.strip() or result.stdout.strip())
        if not json_output:
            return result.stdout.strip()
        text = result.stdout.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise BeadsError(f"Beads did not return JSON: {text}") from error

    def create(self, title: str, description: str, issue_type: str, priority: str = "2", parent: str | None = None, metadata: dict[str, Any] | None = None) -> str:
        args = ["create", title, "--description", description, "--type", issue_type, "--priority", priority, "--silent"]
        if parent:
            args.extend(["--parent", parent])
        if metadata:
            args.extend(["--metadata", json.dumps(metadata)])
        return str(self.run(*args)).strip()

    def show(self, issue_id: str) -> dict[str, Any]:
        value = self.run("show", issue_id, json_output=True)
        if isinstance(value, list):
            if not value:
                raise BeadsError(f"Bead {issue_id} was not found.")
            return value[0]
        if not isinstance(value, dict):
            raise BeadsError(f"Bead {issue_id} has an unexpected shape.")
        return value

    def list(self, *args: str) -> list[dict[str, Any]]:
        value = self.run("list", *args, json_output=True)
        if value is None:
            return []
        if isinstance(value, dict):
            value = value.get("issues", value.get("data", []))
        return list(value) if isinstance(value, list) else []

    def all(self) -> list[dict[str, Any]]:
        """Return one complete view of the native Beads issue store."""
        return self.list("--all", "--limit", "0")

    def status_snapshot(self) -> list[dict[str, Any]]:
        """Read every TUI field in one read-only Dolt SQL statement.

        Beads runs this repository in embedded-Dolt mode, where ``bd sql`` is
        unavailable.  Dolt itself can still query the embedded data directory.
        This method is deliberately read-only and is for the dashboard only;
        all work mutations continue to use native ``bd`` commands.
        """
        metadata_path = self.root / ".beads" / "metadata.json"
        try:
            database = json.loads(metadata_path.read_text())["dolt_database"]
        except (OSError, json.JSONDecodeError, KeyError) as error:
            raise BeadsError(f"Could not identify the embedded Beads database: {error}") from error
        query = f'''USE `{database}`;
SELECT
  i.id, i.title, i.status, i.priority, i.issue_type, i.assignee,
  i.created_at, i.updated_at, i.closed_at,
  (r.id IS NOT NULL) AS is_ready,
  (SELECT d.depends_on_issue_id
     FROM dependencies d
    WHERE d.issue_id = i.id AND d.type = 'parent-child'
    LIMIT 1) AS parent,
  COALESCE((SELECT JSON_ARRAYAGG(JSON_OBJECT(
      'depends_on_id', d.depends_on_issue_id,
      'type', d.type
    ))
    FROM dependencies d
   WHERE d.issue_id = i.id), JSON_ARRAY()) AS dependencies
FROM issues i
LEFT JOIN ready_issues r ON r.id = i.id
ORDER BY i.priority, i.id'''
        result = subprocess.run(
            ["dolt", "--data-dir", str(self.root / ".beads" / "embeddeddolt"), "sql", "-q", query, "-r", "json"],
            cwd=self.root,
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise BeadsError(result.stderr.strip() or "Dolt could not read the Beads status snapshot.")
        try:
            value = json.loads(result.stdout)
            rows = value.get("rows", [])
        except (json.JSONDecodeError, AttributeError) as error:
            raise BeadsError("Dolt did not return a JSON status snapshot.") from error
        return list(rows) if isinstance(rows, list) else []

    def ready(self) -> list[dict[str, Any]]:
        return [issue for issue in self.list() if self.is_runnable(issue)]

    def runnable_from(self, issues: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Find runnable issues from one complete Beads list response."""
        issue_list = list(issues)
        by_id = {issue_id(issue): issue for issue in issue_list}
        return [issue for issue in issue_list if self.is_runnable_from(issue, by_id)]

    @staticmethod
    def is_runnable_from(issue: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
        if issue.get("status") != "open":
            return False
        parent = issue.get("parent")
        if parent and by_id.get(str(parent), {}).get("status") != "closed":
            return False
        for dependency in issue.get("dependencies", []):
            blocker = dependency.get("depends_on_id")
            if blocker and by_id.get(str(blocker), {}).get("status") != "closed":
                return False
        return True

    def is_runnable(self, issue: dict[str, Any]) -> bool:
        """Return true only when live parent and blocker records are closed."""
        if issue.get("status") != "open":
            return False
        parent = issue.get("parent")
        if parent and self.show(str(parent)).get("status") != "closed":
            return False
        for dependency in issue.get("dependencies", []):
            blocker = dependency.get("depends_on_id")
            if blocker and self.show(str(blocker)).get("status") != "closed":
                return False
        return True

    def update(self, issue_id: str, *args: str) -> None:
        self.run("update", issue_id, *args)

    def close(self, issue_id: str, reason: str) -> None:
        self.run("close", issue_id, "--reason", reason)

    def comment(self, issue_id: str, text: str) -> None:
        self.run("comments", "add", issue_id, text)

    def add_dependency(self, issue_id: str, blocker_id: str) -> None:
        self.run("dep", "add", issue_id, blocker_id)


def issue_id(issue: dict[str, Any]) -> str:
    return str(issue.get("id", ""))


def issue_type(issue: dict[str, Any]) -> str:
    return str(issue.get("issue_type", issue.get("type", "task")))


def issue_priority(issue: dict[str, Any]) -> int:
    value = issue.get("priority", 4)
    try:
        return int(str(value).removeprefix("P"))
    except ValueError:
        return 4


def sort_issues(issues: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(issues, key=lambda issue: (issue_priority(issue), issue_id(issue)))
