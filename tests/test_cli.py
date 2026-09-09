import unittest
import json
import time
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

from asco.beads import Beads
from asco.cli import command_respond
from asco.runner import Runner
from asco.tui import AscoTui


class ReadinessTests(unittest.TestCase):
    def test_status_snapshot_uses_one_read_only_dolt_query(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            beads_dir = root / ".beads"
            beads_dir.mkdir()
            (beads_dir / "metadata.json").write_text(json.dumps({"dolt_database": "test_beads"}))
            result = SimpleNamespace(returncode=0, stdout=json.dumps({"rows": [{"id": "asco-work", "is_ready": True}]}), stderr="")

            with patch("asco.beads.subprocess.run", return_value=result) as run:
                snapshot = Beads(root).status_snapshot()

            self.assertEqual(snapshot, [{"id": "asco-work", "is_ready": True}])
            command = run.call_args.args[0]
            self.assertEqual(command[:4], ["dolt", "--data-dir", str(beads_dir / "embeddeddolt"), "sql"])
            self.assertIn("LEFT JOIN ready_issues", command[5])
            self.assertIn("FROM issues i", command[5])

    def test_child_waits_for_open_parent(self):
        beads = Mock(spec=Beads)
        beads.show.return_value = {"status": "open"}
        issue = {"status": "open", "parent": "asco-parent", "dependencies": []}

        self.assertFalse(Beads.is_runnable(beads, issue))

    def test_issue_runs_after_closed_blockers(self):
        beads = Mock(spec=Beads)
        beads.show.return_value = {"status": "closed"}
        issue = {"status": "open", "dependencies": [{"depends_on_id": "asco-blocker"}]}

        self.assertTrue(Beads.is_runnable(beads, issue))

    def test_runnable_from_uses_a_single_issue_snapshot(self):
        issues = [
            {"id": "asco-closed", "status": "closed"},
            {"id": "asco-ready", "status": "open", "dependencies": [{"depends_on_id": "asco-closed"}]},
            {"id": "asco-blocked", "status": "open", "dependencies": [{"depends_on_id": "asco-ready"}]},
        ]

        runnable = Beads(Path(".")).runnable_from(issues)

        self.assertEqual([issue["id"] for issue in runnable], ["asco-ready"])


class EscalationResponseTests(unittest.TestCase):
    def test_response_closes_escalation_and_reopens_blocked_task(self):
        beads = Mock(spec=Beads)
        beads.show.return_value = {
            "issue_type": "escalation",
            "status": "open",
            "metadata": {"asco": {"blocked_task": "asco-work"}},
        }
        args = Mock(issue="asco-ceo", message="Ship the first version.")

        self.assertEqual(command_respond(args, beads), 0)
        beads.comment.assert_any_call("asco-ceo", "CEO decision: Ship the first version.")
        beads.close.assert_called_once_with("asco-ceo", "CEO resolved the escalation.")
        beads.update.assert_called_once_with("asco-work", "--status", "open", "--assignee", "")


class TuiLogTests(unittest.TestCase):
    def test_snapshot_uses_one_complete_beads_read_and_keeps_queues_exclusive(self):
        beads = Mock(spec=Beads)
        engineering = {"id": "asco-engineering", "status": "open", "issue_type": "engineering", "dependencies": [{"depends_on_id": "asco-escalation"}]}
        escalation = {"id": "asco-escalation", "status": "closed", "issue_type": "escalation"}
        running = {"id": "asco-running", "status": "in_progress", "issue_type": "engineering", "assignee": "asco-runner"}
        waiting = {"id": "asco-waiting", "status": "open", "issue_type": "engineering", "dependencies": [{"depends_on_id": "asco-running"}]}
        engineering["is_ready"] = True
        escalation["is_ready"] = False
        running["is_ready"] = False
        waiting["is_ready"] = False
        beads.status_snapshot.return_value = [engineering, escalation, running, waiting]

        snapshot = AscoTui(Path("."), beads).snapshot()

        self.assertEqual([issue["id"] for issue in snapshot["Ready"]], ["asco-engineering"])
        self.assertEqual([issue["id"] for issue in snapshot["Running"]], ["asco-running"])
        self.assertEqual([issue["id"] for issue in snapshot["Waiting"]], ["asco-waiting"])
        self.assertEqual(sum(len(issues) for issues in snapshot.values()), 4)
        beads.status_snapshot.assert_called_once_with()
        beads.list.assert_not_called()

    def test_log_command_targets_the_configured_tmux_pane(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / ".asco" / "logs" / "asco-work.log"
            log_path.parent.mkdir(parents=True)
            log_path.write_text("Codex output\n")
            tui = AscoTui(root, Mock(spec=Beads), "company")
            tui.selected_issue_id = "asco-work"
            lookup = Mock(returncode=0, stdout="%9\n")
            sent = Mock(returncode=0, stderr="")
            with patch("asco.tui.subprocess.run", side_effect=[lookup, sent]) as run:
                tui.open_selected_log()

            self.assertEqual(tui.message, "Opened asco-work output in the lower-right logs pane.")
            self.assertEqual(run.call_args_list[1].args[0][:4], ["tmux", "send-keys", "-t", "%9"])

    def test_status_refresh_notifies_the_event_loop(self):
        tui = AscoTui(Path("."), Mock(spec=Beads))
        tui.collect_status = Mock(return_value=({"Needs CEO": [], "Running": [], "Ready": [{"id": "asco-work"}], "Waiting": [], "Blocked": [], "Done": []}, {}))
        stop = Event()
        notified = Event()

        def notify():
            notified.set()
            stop.set()

        tui.refresh_status(stop, notify, interval=60)

        self.assertTrue(notified.is_set())
        self.assertEqual(tui.status()[0]["Ready"][0]["id"], "asco-work")

    def test_draw_uses_the_current_window_size_for_clipping(self):
        tui = AscoTui(Path("."), Mock(spec=Beads))
        tui._status = {"Needs CEO": [], "Running": [], "Ready": [{"id": "asco-work", "title": "A title that is wider than the window"}], "Waiting": [], "Blocked": [], "Done": []}
        screen = Mock()
        screen.getmaxyx.return_value = (5, 20)

        tui.draw(screen)

        writes = [call.args for call in screen.addnstr.call_args_list]
        self.assertTrue(writes)
        self.assertTrue(all(args[3] <= 19 - args[1] for args in writes))

    def test_draw_uses_the_ceo_heading_style_for_ceo_requests(self):
        tui = AscoTui(Path("."), Mock(spec=Beads))
        tui._ceo_heading_style = 42
        tui._status = {"Needs CEO": [], "Running": [], "Ready": [], "Waiting": [], "Blocked": [], "Done": []}
        screen = Mock()
        screen.getmaxyx.return_value = (12, 80)

        tui.draw(screen)

        self.assertIn((2, 0, "Needs CEO (0)", 79, 42), [call.args for call in screen.addnstr.call_args_list])

    def test_draw_reports_when_beads_has_no_live_engineer_process(self):
        tui = AscoTui(Path("."), Mock(spec=Beads))
        tui._status = {"Needs CEO": [], "Running": [{"id": "asco-work", "status": "in_progress", "issue_type": "engineering", "assignee": "asco-runner"}], "Ready": [], "Waiting": [], "Blocked": [], "Done": []}
        tui._runner_status = {"state": "idle", "runner_pid": 1234, "updated_at": time.time()}
        screen = Mock()
        screen.getmaxyx.return_value = (12, 100)

        tui.draw(screen)

        writes = [call.args[2] for call in screen.addnstr.call_args_list]
        self.assertIn("Runner process: idle | PID: 1234 | checked 0s ago | Beads task: asco-work", writes)
        self.assertIn("Engineer process: none | Beads task asco-work is in progress", writes)


class EngineerPromptTests(unittest.TestCase):
    def test_audit_instruction_is_recursive(self):
        prompt = (Path(__file__).resolve().parents[1] / "prompts" / "engineer.md").read_text()

        self.assertIn("If you create any correction tasks, file a successor Audit Bead", prompt)
        self.assertIn("Never make an Audit depend on work that it created.", prompt)


class AuditWorktreeTests(unittest.TestCase):
    def test_runner_status_includes_the_dispatcher_pid(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Runner(root, Mock(spec=Beads), root / "prompts" / "engineer.md")

            runner.write_status("idle", "No task is running.")

            status = json.loads((root / ".asco" / "runner-status.json").read_text())
            self.assertIsInstance(status["runner_pid"], int)
            self.assertEqual(status["state"], "idle")

    def test_audit_uses_its_audited_task_worktree(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            audited_worktree = root / ".asco" / "worktrees" / "asco-engineering"
            audited_worktree.mkdir(parents=True)
            runner = Runner(root, Mock(spec=Beads), root / "prompts" / "engineer.md")
            audit = {"id": "asco-audit", "issue_type": "audit", "metadata": {"asco": {"audit_of": "asco-engineering"}}}

            self.assertEqual(runner.worktree_for(audit), audited_worktree)

    def test_runner_resumes_a_task_already_assigned_to_itself(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            beads = Mock(spec=Beads)
            task = {"id": "asco-work", "status": "open", "issue_type": "engineering", "assignee": "asco-runner", "priority": 2}
            beads.ready.return_value = [task]
            beads.show.return_value = task
            runner = Runner(root, beads, root / "prompts" / "engineer.md")

            runner.claim_next()

            beads.update.assert_called_once_with("asco-work", "--status", "in_progress")

    def test_runner_ignores_a_task_with_a_custom_audit_role(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            beads = Mock(spec=Beads)
            task = {"id": "asco-audit", "status": "open", "issue_type": "task", "priority": 2, "metadata": {"asco": {"role": "audit", "audit_of": "asco-engineering"}}}
            beads.ready.return_value = [task]
            runner = Runner(root, beads, root / "prompts" / "engineer.md")

            self.assertIsNone(runner.claim_next())

            beads.update.assert_not_called()
