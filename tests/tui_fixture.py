"""Run the real TUI with a deterministic status model for PTY tests."""

from pathlib import Path

from asco.tui import AscoTui


class FixtureBeads:
    def status_snapshot(self):
        return [
            {"id": "asco-ceo", "title": "CEO decision", "status": "open", "issue_type": "escalation", "is_ready": False},
            {"id": "asco-running", "title": "Running work", "status": "in_progress", "issue_type": "engineering", "is_ready": False},
            {"id": "asco-ready", "title": "Ready work", "status": "open", "issue_type": "engineering", "is_ready": True},
            {"id": "asco-blocked", "title": "Blocked work", "status": "open", "issue_type": "engineering", "is_ready": False, "dependencies": [{"depends_on_id": "asco-running"}]},
        ]


AscoTui(Path(__file__).parent, FixtureBeads()).run()
