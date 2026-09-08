"""The single-worker dispatcher that turns ready Beads into Codex sessions."""

from __future__ import annotations

import fcntl
import json
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .beads import Beads, BeadsError, issue_id, issue_type, sort_issues


class Runner:
    def __init__(self, root: Path, beads: Beads, prompt_path: Path):
        self.root = root
        self.beads = beads
        self.prompt_path = prompt_path
        self.state_dir = root / ".asco"
        self.state_dir.mkdir(exist_ok=True)

    @contextmanager
    def lock(self):
        path = self.state_dir / "runner.lock"
        with path.open("w") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("The Asco runner is already active.") from error
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def claim_next(self) -> dict | None:
        candidates = [issue for issue in self.beads.ready() if issue_type(issue) in {"engineering", "audit"}]
        if not candidates:
            return None
        issue = sort_issues(candidates)[0]
        task_id = issue_id(issue)
        self.beads.update(task_id, "--claim", "--assignee", "asco-runner")
        self.beads.comment(task_id, "Asco runner claimed this task.")
        return self.beads.show(task_id)

    def worktree_for(self, issue: dict) -> Path:
        task_id = issue_id(issue)
        worktree = self.state_dir / "worktrees" / task_id
        if worktree.exists():
            return worktree
        worktree.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "worktree", "add", "-b", f"asco/{task_id}", str(worktree), "HEAD"], cwd=self.root, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Could not create task worktree.")
        return worktree

    def prompt_for(self, issue: dict, workdir: Path) -> str:
        return f"{self.prompt_path.read_text()}\n\nYou have been assigned Bead {issue_id(issue)}.\nYour working directory is {workdir}.\nTask record:\n{json.dumps(issue, indent=2, sort_keys=True)}\n"

    def run_one(self) -> bool:
        issue = self.claim_next()
        if issue is None:
            return False
        task_id = issue_id(issue)
        try:
            workdir = self.worktree_for(issue) if issue_type(issue) == "engineering" else self.root
            result = subprocess.run(["codex", "exec", "--approve-for-me", "--cd", str(workdir), self.prompt_for(issue, workdir)], cwd=workdir)
        except Exception as error:
            self.beads.comment(task_id, f"Asco runner could not start Codex: {error}")
            raise
        if result.returncode:
            self.beads.comment(task_id, f"Codex exited with status {result.returncode}. The task remains in progress for review.")
        else:
            self.beads.comment(task_id, "Codex exited normally. The task remains open until it records completion evidence.")
        return True

    def run_forever(self, interval: float = 5.0) -> None:
        with self.lock():
            while True:
                try:
                    found_work = self.run_one()
                except BeadsError as error:
                    raise RuntimeError(f"Beads rejected a runner action: {error}") from error
                if not found_work:
                    time.sleep(interval)
