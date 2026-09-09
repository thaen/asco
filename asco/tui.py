"""A small CEO-facing terminal view of the Beads graph."""

from __future__ import annotations

import curses
import json
import os
import select
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .beads import Beads, issue_id, issue_type, sort_issues


class AscoTui:
    def __init__(self, root: Path, beads: Beads, tmux_session: str = "asco-company"):
        self.root = root
        self.beads = beads
        self.tmux_session = tmux_session
        self.message = "Status view only. It refreshes every second. Press q to quit."
        self.selected_issue_id: str | None = None
        self.visible_issues: list[dict] = []
        self._status_lock = threading.Lock()
        self._status = self.empty_snapshot()
        self._status_error: str | None = None
        self._status_loaded = False
        self._runner_status: dict[str, object] = {}
        self._issues_by_id: dict[str, dict] = {}
        self._ceo_heading_style = curses.A_BOLD

    @staticmethod
    def empty_snapshot() -> dict[str, list[dict]]:
        return {"Needs CEO": [], "Running": [], "Ready": [], "Waiting": [], "Blocked": [], "Done": []}

    def collect_status(self) -> tuple[dict[str, list[dict]], dict[str, dict]]:
        """Build one exclusive display from one complete Beads SQL read.

        The runner status file is intentionally absent from this method.  It
        describes a local process, while this snapshot describes work.
        """
        all_issues = self.beads.status_snapshot()
        ready = {issue_id(issue) for issue in all_issues if issue.get("is_ready")}
        queues = self.empty_snapshot()
        for issue in all_issues:
            status = issue.get("status")
            if issue_type(issue) == "escalation" and status != "closed":
                queue = "Needs CEO"
            elif status == "in_progress":
                queue = "Running"
            elif status == "blocked":
                queue = "Blocked"
            elif status == "closed":
                queue = "Done"
            elif issue_id(issue) in ready:
                queue = "Ready"
            else:
                queue = "Waiting"
            queues[queue].append(issue)
        return {name: sort_issues(issues) for name, issues in queues.items()}, {issue_id(issue): issue for issue in all_issues}

    def snapshot(self) -> dict[str, list[dict]]:
        return self.collect_status()[0]

    def refresh_status(self, stop: threading.Event, notify: Callable[[], None], interval: float = 1.0) -> None:
        """Read Beads away from the curses input loop."""
        while not stop.is_set():
            try:
                status, issues_by_id = self.collect_status()
            except Exception as error:
                with self._status_lock:
                    self._status_error = str(error)
            else:
                with self._status_lock:
                    self._status = status
                    self._status_error = None
                    self._status_loaded = True
                    self._runner_status = self.read_runner_status()
                    self._issues_by_id = issues_by_id
            notify()
            stop.wait(interval)

    def read_runner_status(self) -> dict[str, object]:
        try:
            return json.loads((self.root / ".asco" / "runner-status.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def status(self) -> tuple[dict[str, list[dict]], str | None, bool, dict[str, object], dict[str, dict]]:
        with self._status_lock:
            return self._status, self._status_error, self._status_loaded, self._runner_status, self._issues_by_id

    def move_selection(self, amount: int) -> None:
        if not self.visible_issues:
            return
        ids = [issue_id(issue) for issue in self.visible_issues]
        current = ids.index(self.selected_issue_id) if self.selected_issue_id in ids else 0
        self.selected_issue_id = ids[(current + amount) % len(ids)]

    def open_selected_log(self) -> None:
        if not self.selected_issue_id:
            self.message = "No issue is selected."
            return
        log_path = self.root / ".asco" / "logs" / f"{self.selected_issue_id}.log"
        if not log_path.exists():
            self.message = f"No output log exists for {self.selected_issue_id}."
            return
        target = subprocess.run(
            ["tmux", "show-options", "-v", "-t", self.tmux_session, "@asco_log_pane"],
            text=True,
            capture_output=True,
        )
        pane = target.stdout.strip()
        if target.returncode or not pane:
            self.message = f"The logs pane is unavailable in tmux session {self.tmux_session}."
            return
        command = f"less +G -- {shlex.quote(str(log_path))}"
        sent = subprocess.run(["tmux", "send-keys", "-t", pane, command, "Enter"], text=True, capture_output=True)
        if sent.returncode:
            self.message = sent.stderr.strip() or f"Asco could not send the pager command for {self.selected_issue_id}."
            return
        self.message = f"Opened {self.selected_issue_id} output in the lower-right logs pane."

    def draw(self, screen: curses.window) -> None:
        height, width = screen.getmaxyx()

        def write(row: int, column: int, text: str, style: int = curses.A_NORMAL) -> None:
            if row < 0 or row >= height or column < 0 or column >= width - 1:
                return
            try:
                screen.addnstr(row, column, text, width - column - 1, style)
            except curses.error:
                pass

        screen.erase()
        write(0, 0, "Asco — A Software Company", curses.A_BOLD)
        row = 2
        visible: list[dict] = []
        status, error, loaded, runner_status, issues_by_id = self.status()
        show_details = height >= 11
        content_bottom = height - 5 if show_details else height - 1
        for name, issues in status.items():
            if row >= content_bottom:
                break
            heading_style = self._ceo_heading_style if name == "Needs CEO" else curses.A_BOLD
            write(row, 0, f"{name} ({len(issues)})", heading_style)
            row += 1
            for issue in issues:
                if row >= content_bottom:
                    break
                visible.append(issue)
                title = str(issue.get("title", ""))
                style = curses.A_REVERSE if issue_id(issue) == self.selected_issue_id else curses.A_NORMAL
                write(row, 2, f"{issue_id(issue):<12} {title}", style)
                row += 1
            row += 1
        self.visible_issues = visible
        if self.selected_issue_id not in {issue_id(issue) for issue in visible}:
            self.selected_issue_id = issue_id(visible[0]) if visible else None
        if error:
            footer = f"Status refresh failed: {error}"
        elif not loaded:
            footer = "Loading Beads status..."
        else:
            selection = self.selected_issue_id or "none"
            footer = f"Selected: {selection} | {self.message}"
        selected = issues_by_id.get(self.selected_issue_id or "")
        runner_state = str(runner_status.get("state", "unknown"))
        runner_detail = str(runner_status.get("detail", "No runner status file exists."))
        runner_pid = runner_status.get("runner_pid")
        engineer_pid = runner_status.get("engineer_pid")
        last_task = runner_status.get("task_id")
        exit_code = runner_status.get("exit_code")
        runner_task = next(
            (
                issue_id(issue)
                for issue in status["Running"]
                if issue.get("assignee") == "asco-runner"
            ),
            None,
        )
        if show_details:
            process_line = f"Runner process: {runner_state}"
            if runner_pid:
                process_line += f" | PID: {runner_pid}"
            try:
                heartbeat_age = max(0, int(time.time() - float(runner_status["updated_at"])))
            except (KeyError, TypeError, ValueError):
                heartbeat_age = None
            if heartbeat_age is not None:
                process_line += f" | checked {heartbeat_age}s ago"
            if runner_task:
                process_line += f" | Beads task: {runner_task}"
            elif runner_state == "working":
                process_line += f" | mismatch: process reports {runner_detail}; Beads reports no runner task"
            write(height - 5, 0, process_line)
            if engineer_pid:
                engineer_line = f"Engineer process: PID {engineer_pid} | task: {last_task or 'unknown'}"
            elif runner_task:
                engineer_line = f"Engineer process: none | Beads task {runner_task} is in progress"
            elif last_task and exit_code is not None:
                engineer_line = f"Last Engineer: {last_task} exited with status {exit_code}"
            else:
                engineer_line = "Engineer process: none"
            write(height - 4, 0, engineer_line)
        if selected and show_details:
            assignment = selected.get("assignee") or "unassigned"
            write(height - 3, 0, f"Task: {issue_id(selected)} | {issue_type(selected)} | {selected.get('status')} | assignee: {assignment}")
            dependencies = selected.get("dependencies", [])
            dependency_text = ", ".join(f"{dependency.get('depends_on_id')}={issues_by_id.get(str(dependency.get('depends_on_id')), {}).get('status', 'unknown')}" for dependency in dependencies) or "none"
            parent = selected.get("parent") or "none"
            write(height - 2, 0, f"Parent: {parent} | depends on: {dependency_text}")
        write(height - 1, 0, footer)
        screen.refresh()

    def run(self) -> None:
        stop = threading.Event()
        resized = threading.Event()
        wake_read, wake_write = os.pipe()
        os.set_blocking(wake_read, False)

        def notify() -> None:
            try:
                os.write(wake_write, b".")
            except BlockingIOError:
                pass
            except OSError:
                pass

        refresh = threading.Thread(target=self.refresh_status, args=(stop, notify), daemon=True)
        refresh_started = False
        previous_resize_handler = signal.getsignal(signal.SIGWINCH)

        def resize_handler(signum: int, frame: object) -> None:
            resized.set()
            notify()

        def resize_screen(screen: curses.window) -> None:
            try:
                size = os.get_terminal_size(sys.stdin.fileno())
                curses.resizeterm(size.lines, size.columns)
            except (curses.error, OSError):
                pass
            screen.erase()

        def loop(screen: curses.window) -> None:
            nonlocal refresh_started
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            if curses.has_colors():
                curses.start_color()
                try:
                    curses.use_default_colors()
                    curses.init_pair(1, curses.COLOR_CYAN, -1)
                except curses.error:
                    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
                self._ceo_heading_style = curses.A_BOLD | curses.color_pair(1)
            screen.timeout(0)
            signal.signal(signal.SIGWINCH, resize_handler)
            refresh.start()
            refresh_started = True
            self.draw(screen)
            while True:
                readable, _, _ = select.select([sys.stdin.fileno(), wake_read], [], [])
                changed = False
                resized_this_cycle = False
                if wake_read in readable:
                    while True:
                        try:
                            if not os.read(wake_read, 4096):
                                break
                        except BlockingIOError:
                            break
                if resized.is_set():
                    resized.clear()
                    resize_screen(screen)
                    resized_this_cycle = True
                    changed = True
                if sys.stdin.fileno() in readable:
                    screen.timeout(50)
                else:
                    screen.timeout(0)
                while True:
                    key = screen.getch()
                    if key == curses.ERR:
                        break
                    changed = True
                    if key == curses.KEY_RESIZE:
                        resized.clear()
                        if not resized_this_cycle:
                            resize_screen(screen)
                            resized_this_cycle = True
                        continue
                    if key == ord("q"):
                        return
                    if key == curses.KEY_UP:
                        self.move_selection(-1)
                    if key == curses.KEY_DOWN:
                        self.move_selection(1)
                    if key in {ord("l"), ord("L")}:
                        self.open_selected_log()
                screen.timeout(0)
                if changed or wake_read in readable:
                    self.draw(screen)

        try:
            curses.wrapper(loop)
        finally:
            stop.set()
            if refresh_started:
                refresh.join(timeout=2)
            signal.signal(signal.SIGWINCH, previous_resize_handler)
            os.close(wake_read)
            os.close(wake_write)
