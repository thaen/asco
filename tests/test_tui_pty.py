"""Pseudo-terminal tests for the real curses event loop."""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

import pexpect
import pyte


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "tui_fixture.py"


class TerminalCapture:
    def __init__(self, columns: int, lines: int):
        self.screen = pyte.Screen(columns, lines)
        self.stream = pyte.Stream(self.screen)

    def write(self, data: str) -> None:
        self.stream.feed(data)

    def flush(self) -> None:
        pass

    def text(self) -> str:
        return "\n".join(self.screen.display)


class TuiPtyTests(unittest.TestCase):
    def wait_for_screen_text(self, child: pexpect.spawn, terminal: TerminalCapture, text: str) -> None:
        deadline = time.monotonic() + child.timeout
        while text not in terminal.text():
            if time.monotonic() >= deadline:
                self.fail(f"The terminal did not show {text!r}. Current screen:\n{terminal.text()}")
            try:
                child.read_nonblocking(size=4096, timeout=0.1)
            except pexpect.TIMEOUT:
                pass

    def start_tui(self) -> tuple[pexpect.spawn, TerminalCapture]:
        environment = os.environ.copy()
        environment["TERM"] = "xterm-256color"
        environment["PYTHONPATH"] = str(ROOT)
        child = pexpect.spawn(sys.executable, [str(FIXTURE)], cwd=str(ROOT), env=environment, encoding="utf-8", dimensions=(12, 80), timeout=5)
        self.addCleanup(lambda: child.isalive() and child.terminate(force=True))
        terminal = TerminalCapture(80, 12)
        child.logfile_read = terminal
        self.wait_for_screen_text(child, terminal, "Selected: asco-ceo")
        return child, terminal

    def test_arrow_key_changes_the_selected_issue(self):
        child, terminal = self.start_tui()

        child.send("\x1bOB")

        self.wait_for_screen_text(child, terminal, "Selected: asco-running")

    def test_l_key_reports_missing_output_for_selected_issue(self):
        child, terminal = self.start_tui()

        child.send("l")

        self.wait_for_screen_text(child, terminal, "No output log exists for asco-ceo.")

    def test_resize_keeps_the_tui_running_and_accepts_quit(self):
        child, terminal = self.start_tui()

        child.setwinsize(6, 24)
        terminal.screen.resize(6, 24)
        child.send("l")
        self.wait_for_screen_text(child, terminal, "Selected: asco-ceo | No")
        child.send("q")
        child.expect(pexpect.EOF)
