import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("asco", Path(__file__).parents[1] / "src" / "asco.py")
asco = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(asco)


class ScriptedDashboardScreen:
    def __init__(self, keys):
        self.keys = list(keys)
        self.drawn = []

    def draw(self, status):
        self.drawn.append(status)

    def getch(self):
        return self.keys.pop(0)


class SnapshotReader:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = []

    def __call__(self):
        snapshot = self.snapshots.pop(0)
        self.calls.append(snapshot)
        return snapshot


class AscoTests(unittest.TestCase):
    def run_dashboard(self, keys, snapshots):
        screen = ScriptedDashboardScreen(keys)
        reader = SnapshotReader(snapshots)
        delays = []
        controller = asco.DashboardController(
            reader,
            lambda snapshot, show_all: "%s all_closed=%s" % (snapshot, show_all),
            screen,
            lambda: delays.append(None),
        )
        controller.run()
        return screen, reader, delays

    def test_dashboard_quit_reads_a_fresh_snapshot_before_exit(self):
        screen, reader, delays = self.run_dashboard(
            [ord("q")], ["initial", "quit refresh"]
        )
        self.assertEqual(reader.calls, ["initial", "quit refresh"])
        self.assertEqual(screen.drawn, ["initial all_closed=False"])
        self.assertEqual(delays, [])

    def test_dashboard_idle_ticks_keep_the_initial_snapshot(self):
        screen, reader, delays = self.run_dashboard(
            [-1, -1, ord("q")], ["initial", "quit refresh"]
        )
        self.assertEqual(reader.calls, ["initial", "quit refresh"])
        self.assertEqual(screen.drawn, ["initial all_closed=False"] * 3)
        self.assertEqual(delays, [None, None])

    def test_dashboard_closed_toggle_refreshes_and_draws_changed_view(self):
        screen, reader, delays = self.run_dashboard(
            [ord("c"), ord("q")],
            ["recent closed items", "all closed items", "quit refresh"],
        )
        self.assertEqual(reader.calls, ["recent closed items", "all closed items", "quit refresh"])
        self.assertEqual(screen.drawn, [
            "recent closed items all_closed=False",
            "all closed items all_closed=True",
        ])
        self.assertEqual(delays, [])

    def test_task_paths_are_under_common_state_directory(self):
        worktree, branch, log = asco.task_paths("/project", "bd-42", "bd-1")
        self.assertEqual(worktree, Path("/project/.asco/worktrees/tasks/bd-42"))
        self.assertEqual(branch, "asco/task-bd-42")
        self.assertEqual(log, Path("/project/.asco/logs/bd-42.log"))

    def test_registered_worktrees_parses_git_porcelain(self):
        class Result:
            returncode = 0
            stdout = "worktree /project\nHEAD abc\n\nworktree /project/.asco/worktrees/tasks/bd-1\nHEAD def\n"
        with patch.object(asco, "command", return_value=Result()):
            paths = asco.registered_worktrees("/project")
        self.assertEqual(paths, {
            Path("/project").resolve(),
            Path("/project/.asco/worktrees/tasks/bd-1").resolve(),
        })

    def test_parent_id_handles_beads_shapes(self):
        self.assertEqual(asco.parent_id({"parent": {"id": "bd-1"}}), "bd-1")
        self.assertEqual(asco.parent_id({"parent_id": "bd-2"}), "bd-2")

    def test_beads_issue_type_and_legacy_asco_metadata_are_normalized(self):
        issue = {"issue_type": "epic", "metadata": {"asco": {"worktree": "/tmp/epic"}}}
        self.assertEqual(asco.issue_type(issue), "epic")
        self.assertEqual(asco.metadata(issue)["asco_worktree"], "/tmp/epic")

    def test_metadata_true_handles_beads_boolean_and_string_values(self):
        self.assertTrue(asco.metadata_true({"asco_merged": True}, "asco_merged"))
        self.assertTrue(asco.metadata_true({"asco_merged": "true"}, "asco_merged"))
        self.assertFalse(asco.metadata_true({"asco_merged": False}, "asco_merged"))

    def test_legacy_parent_is_not_an_asco_epic(self):
        runner = asco.Runner("/project", 2)
        parent = {"id": "bd-1", "issue_type": "engineering"}
        child = {"id": "bd-2", "parent": "bd-1"}
        self.assertIsNone(runner.epic_for(child, [parent, child]))

    def test_escalation_is_not_dispatchable(self):
        runner = asco.Runner("/project", 2)
        epic = {"id": "bd-1", "issue_type": "epic"}
        escalation = {"id": "bd-2", "issue_type": "escalation", "parent": "bd-1"}
        self.assertEqual(runner.dispatchable_tasks([epic, escalation], [epic, escalation]), [epic])

    def test_blocking_ids_handles_beads_dependency_records(self):
        self.assertEqual(asco.blocking_ids({"blocked_by": [{"depends_on_id": "bd-1"}, "bd-2"]}), ["bd-1", "bd-2"])

    def test_escalation_detection_uses_type_or_label(self):
        self.assertTrue(asco.is_escalation({"issue_type": "escalation"}))
        self.assertTrue(asco.is_escalation({"labels": ["escalation"]}))
        self.assertFalse(asco.is_escalation({"issue_type": "task"}))

    @patch.object(asco, "dispatcher_processes", return_value=[])
    @patch.object(asco, "process_alive", return_value=False)
    def test_status_uses_done_and_dependency_blocked(self, alive, dispatchers):
        snapshot = ([
            {"id": "bd-1", "status": "open", "title": "Wait"},
            {"id": "bd-2", "status": "closed", "title": "Finished"},
        ], {"bd-1": {}})
        report = asco.render_status(snapshot, root="/project")
        self.assertIn("dependency-blocked", report)
        self.assertIn("Done", report)
        self.assertIn("Assigned", report)

    @patch.object(asco, "dispatcher_processes", return_value=[])
    @patch.object(asco, "process_alive", return_value=False)
    def test_status_reports_beads_assignment(self, alive, dispatchers):
        snapshot = ([{"id": "bd-1", "status": "open", "owner": "engineer-1"}], {})
        self.assertIn("engineer-1", asco.render_status(snapshot, root="/project"))

    @patch.object(asco, "dispatcher_processes", return_value=[])
    @patch.object(asco, "visible_issues", return_value=([{"id": "bd-1", "status": "blocked", "issue_type": "escalation", "title": "Need a choice", "description": "Choose A or B."}], {}))
    @patch.object(asco, "process_alive", return_value=False)
    def test_status_places_waiting_escalation_above_task_table(self, alive, visible, dispatchers):
        report = asco.render_status("/project")
        self.assertIn("Escalations waiting for you:", report)
        self.assertIn("Choose A or B.", report)

    @patch.object(asco, "dispatcher_processes", return_value=["4144  00:01 python asco.py _serve"])
    def test_status_reports_dispatcher_process(self, dispatchers):
        self.assertIn("Dispatcher: running: 4144", asco.render_status(([], {}), root="/project"))

    def test_log_tail_reads_the_most_recent_lines(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / ".asco/logs/runner.log"
            log.parent.mkdir(parents=True)
            log.write_text("one\ntwo\nthree\n", encoding="utf-8")
            self.assertEqual(asco.log_tail(root, 2), ["two", "three"])

    def test_engineer_prompt_names_commit_and_escalation_rules(self):
        prompt = asco.engineer_prompt({"id": "bd-2", "title": "Implement", "description": "Build it"},
                                      {"id": "bd-1", "description": "Feature request"},
                                      Path(__file__).parents[1], Path("/project/worktree"), "asco/task-bd-2",
                                      Path(__file__).parents[1] / ".asco/logs/bd-2.log")
        self.assertIn("Commit every repository change", prompt)
        self.assertIn("type escalation", prompt)
        self.assertIn("asco_blocked_task=bd-2", prompt)
        self.assertIn("--parent bd-1", prompt)

    @patch.object(asco, "process_alive", return_value=True)
    def test_closed_worker_still_counts_until_its_process_exits(self, alive):
        runner = asco.Runner("/project", 2)
        runner.bd.all = lambda: [{"id": "bd-1", "status": "closed", "metadata": {"asco_pid": "12"}}]
        self.assertEqual(runner.worker_count(), 1)

    def test_escalation_type_is_added_without_removing_existing_types(self):
        runner = asco.Runner("/project", 2)
        calls = []
        class Result:
            returncode = 0
            stdout = "review,gate\n"
        def run(*args, **kwargs):
            calls.append(args)
            return Result()
        runner.bd.run = run
        runner.ensure_escalation_type()
        self.assertIn(("config", "set", "types.custom", "review,gate,escalation"), calls)

    def test_answer_reopens_the_blocked_task_and_closes_escalation(self):
        class FakeBeads:
            def __init__(self, root):
                self.calls = []
            def show(self, issue):
                return {"metadata": {"asco_blocked_task": "bd-1"}}
            def comment(self, *args):
                self.calls.append(("comment",) + args)
            def run(self, *args):
                self.calls.append(("run",) + args)
        fake = FakeBeads("/project")
        with patch.object(asco, "Beads", return_value=fake):
            asco.answer_escalation("/project", "bd-2", "Retry it.")
        self.assertIn(("run", "close", "bd-2", "--reason", "The user answered the escalation."), fake.calls)
        self.assertIn(("run", "update", "bd-1", "--status", "open"), fake.calls)

    def test_start_clears_exit_metadata_before_starting_a_retry(self):
        runner = asco.Runner("/project", 2)
        calls = []
        class Result:
            returncode = 0
        def run(*args, **kwargs):
            calls.append(args)
            return Result()
        runner.bd.run = run
        runner.clear_exit_metadata("bd-1")
        self.assertIn(("update", "bd-1", "--unset-metadata", "asco_exited_at", "--unset-metadata", "asco_exit_state", "--unset-metadata", "asco_exit_code"), calls)

    def cleanup_runner(self, worktree):
        runner = asco.Runner("/project", 2)
        class FakeBeads:
            def __init__(self):
                self.metadata = []
            def update_metadata(self, *args, **kwargs):
                self.metadata.append((args, kwargs))
        runner.bd = FakeBeads()
        issues = [
            {"id": "bd-1", "issue_type": "epic", "status": "closed",
             "metadata": {"asco_worktree": "/project/.asco/worktrees/epics/bd-1"}},
            {"id": "bd-2", "issue_type": "task", "parent": "bd-1", "status": "closed",
             "metadata": {"asco_worktree": str(worktree)}},
        ]
        return runner, issues

    @patch.object(asco, "process_alive", return_value=False)
    def test_cleanup_marks_an_already_removed_worktree_clean(self, alive):
        runner, issues = self.cleanup_runner("/project/.asco/worktrees/tasks/bd-2")
        with patch.object(asco, "registered_worktrees", return_value=set()), \
             patch.object(asco, "command") as command_call:
            runner.cleanup_closed_epics(issues)
        command_call.assert_not_called()
        self.assertEqual(runner.bd.metadata[0][0], ("bd-1",))
        self.assertEqual(runner.bd.metadata[0][1]["cleaned"], "true")

    @patch.object(asco, "process_alive", return_value=False)
    def test_cleanup_removes_a_registered_worktree(self, alive):
        path = Path("/project/.asco/worktrees/tasks/bd-2").resolve()
        runner, issues = self.cleanup_runner(path)
        class Result:
            returncode = 0
            stderr = ""
        with patch.object(asco, "registered_worktrees", return_value={path}), \
             patch.object(asco, "command", return_value=Result()) as command_call:
            runner.cleanup_closed_epics(issues)
        command_call.assert_called_once_with(runner.root, ["git", "worktree", "remove", str(path)], check=False)
        self.assertEqual(runner.bd.metadata[0][1]["cleaned"], "true")

    @patch.object(asco, "process_alive", return_value=False)
    def test_cleanup_refuses_an_out_of_scope_worktree(self, alive):
        runner, issues = self.cleanup_runner("/outside/bd-2")
        with patch.object(asco, "registered_worktrees", return_value={Path("/outside/bd-2")}), \
             patch.object(asco, "command") as command_call:
            runner.cleanup_closed_epics(issues)
        command_call.assert_not_called()
        self.assertEqual(runner.bd.metadata, [])


if __name__ == "__main__":
    unittest.main()
