"""A small CEO-facing terminal view of the Beads graph."""

from __future__ import annotations

import curses
from pathlib import Path

from .beads import Beads, issue_id, issue_type, sort_issues


class AscoTui:
    def __init__(self, root: Path, beads: Beads):
        self.root = root
        self.beads = beads
        self.message = "Status view only. It refreshes every second. Press q to quit."

    def snapshot(self) -> dict[str, list[dict]]:
        issues = self.beads.list()
        ready = {issue_id(issue) for issue in self.beads.ready()}
        running = [issue for issue in issues if issue.get("status") == "in_progress"]
        escalations = [issue for issue in issues if issue_type(issue) == "escalation" and issue.get("status") != "closed"]
        queued = [issue for issue in issues if issue_id(issue) in ready and issue.get("status") != "in_progress"]
        blocked = [issue for issue in issues if issue.get("status") == "open" and issue_id(issue) not in ready]
        return {"Needs CEO": sort_issues(escalations), "Running": sort_issues(running), "Ready": sort_issues(queued), "Blocked": sort_issues(blocked)}

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

        curses.wrapper(loop)
