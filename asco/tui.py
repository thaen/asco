"""A small CEO-facing terminal view of the Beads graph."""

from __future__ import annotations

import curses
import subprocess
from pathlib import Path

from .beads import Beads, issue_id, issue_type, sort_issues


class AscoTui:
    def __init__(self, root: Path, beads: Beads):
        self.root = root
        self.beads = beads
        self.runner: subprocess.Popen | None = None
        self.message = "Press r to start the runner, s to stop it, e to resolve an escalation, and q to quit."

    def snapshot(self) -> dict[str, list[dict]]:
        issues = self.beads.list()
        ready = {issue_id(issue) for issue in self.beads.ready()}
        running = [issue for issue in issues if issue.get("status") == "in_progress"]
        escalations = [issue for issue in issues if issue_type(issue) == "escalation" and issue.get("status") != "closed"]
        queued = [issue for issue in issues if issue_id(issue) in ready and issue.get("status") != "in_progress"]
        blocked = [issue for issue in issues if issue.get("status") == "open" and issue_id(issue) not in ready]
        return {"Needs CEO": sort_issues(escalations), "Running": sort_issues(running), "Ready": sort_issues(queued), "Blocked": sort_issues(blocked)}

    def start_runner(self) -> None:
        if self.runner and self.runner.poll() is None:
            self.message = "The runner is already active."
            return
        self.runner = subprocess.Popen(["python3", "-m", "asco.cli", "run"], cwd=self.root)
        self.message = "The runner started."

    def stop_runner(self) -> None:
        if self.runner and self.runner.poll() is None:
            self.runner.terminate()
            self.message = "The runner stopped."
        else:
            self.message = "No runner started by this TUI is active."

    def resolve_first_escalation(self, screen: curses.window) -> None:
        escalations = self.snapshot()["Needs CEO"]
        if not escalations:
            self.message = "No CEO escalation is waiting."
            return
        escalation = escalations[0]
        screen.addstr(curses.LINES - 2, 0, "Resolution: ")
        curses.echo()
        answer = screen.getstr(curses.LINES - 2, 12, 100).decode().strip()
        curses.noecho()
        if not answer:
            self.message = "The escalation remains open because no answer was supplied."
            return
        subprocess.run(["python3", "-m", "asco.cli", "resolve", issue_id(escalation), answer], cwd=self.root, check=True)
        self.message = f"Resolved {issue_id(escalation)}."

    def draw(self, screen: curses.window) -> None:
        screen.erase()
        screen.addstr(0, 0, "Asco — A Software Company", curses.A_BOLD)
        row = 2
        for name, issues in self.snapshot().items():
            screen.addstr(row, 0, f"{name} ({len(issues)})", curses.A_BOLD)
            row += 1
            for issue in issues[:5]:
                title = str(issue.get("title", ""))[: max(1, curses.COLS - 18)]
                screen.addstr(row, 2, f"{issue_id(issue):<12} {title}")
                row += 1
            row += 1
        screen.addstr(curses.LINES - 1, 0, self.message[: curses.COLS - 1])
        screen.refresh()

    def run(self) -> None:
        def loop(screen: curses.window) -> None:
            curses.curs_set(0)
            screen.timeout(1000)
            while True:
                self.draw(screen)
                key = screen.getch()
                if key == ord("q"):
                    return
                if key == ord("r"):
                    self.start_runner()
                if key == ord("s"):
                    self.stop_runner()
                if key == ord("e"):
                    self.resolve_first_escalation(screen)

        curses.wrapper(loop)
