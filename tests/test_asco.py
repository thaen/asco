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
    def dashboard_snapshot(self, name):
        return ([{"id": name, "status": "open", "title": name}], {})

    def run_dashboard(self, keys, snapshots):
        screen = ScriptedDashboardScreen(keys)
        reader = SnapshotReader(snapshots)
        delays = []
        controller = asco.DashboardController(
            reader,
            lambda snapshot, show_all, selected: "%s all_closed=%s selected=%s" % (snapshot, show_all, selected),
            asco.render_task_detail,
            lambda issue: ["log %s" % issue["id"]],
            asco.render_worker_log,
            screen,
            lambda: delays.append(None),
        )
        controller.run()
        return screen, reader, delays

    def test_dashboard_reloads_after_the_source_changes(self):
        screen = ScriptedDashboardScreen([-1])
        snapshot = self.dashboard_snapshot("initial")
        reader = SnapshotReader([snapshot])
        changes = [True]
        controller = asco.DashboardController(
            reader,
            lambda snapshot, show_all, selected: "%s all_closed=%s selected=%s" % (snapshot, show_all, selected),
            asco.render_task_detail,
            lambda issue: ["log %s" % issue["id"]],
            asco.render_worker_log,
            screen,
            lambda: self.fail("The controller must not sleep before it reloads."),
            lambda: changes.pop(0),
        )
        self.assertTrue(controller.run())
        self.assertEqual(reader.calls, [snapshot])
        self.assertEqual(screen.drawn, ["%s all_closed=False selected=initial" % (snapshot,)])

    def test_file_change_detector_notices_a_source_update(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "asco.py"
            source.write_text("first", encoding="utf-8")
            detector = asco.FileChangeDetector(source)
            self.assertFalse(detector())
            source.write_text("second", encoding="utf-8")
            self.assertTrue(detector())

    def test_dashboard_quit_reads_a_fresh_snapshot_before_exit(self):
        screen, reader, delays = self.run_dashboard(
            [ord("q")], [self.dashboard_snapshot("initial"), self.dashboard_snapshot("quit refresh")]
        )
        self.assertEqual(reader.calls, [self.dashboard_snapshot("initial"), self.dashboard_snapshot("quit refresh")])
        self.assertEqual(screen.drawn, ["([{'id': 'initial', 'status': 'open', 'title': 'initial'}], {}) all_closed=False selected=initial"])
        self.assertEqual(delays, [])

    def test_dashboard_idle_ticks_keep_the_initial_snapshot(self):
        screen, reader, delays = self.run_dashboard(
            [-1, -1, ord("q")], [self.dashboard_snapshot("initial"), self.dashboard_snapshot("quit refresh")]
        )
        self.assertEqual(reader.calls, [self.dashboard_snapshot("initial"), self.dashboard_snapshot("quit refresh")])
        self.assertEqual(screen.drawn, ["([{'id': 'initial', 'status': 'open', 'title': 'initial'}], {}) all_closed=False selected=initial"] * 3)
        self.assertEqual(delays, [None, None])

    def test_dashboard_closed_toggle_refreshes_and_draws_changed_view(self):
        screen, reader, delays = self.run_dashboard(
            [ord("c"), ord("q")],
            [self.dashboard_snapshot("recent closed items"), self.dashboard_snapshot("all closed items"), self.dashboard_snapshot("quit refresh")],
        )
        self.assertEqual(reader.calls, [self.dashboard_snapshot("recent closed items"), self.dashboard_snapshot("all closed items"), self.dashboard_snapshot("quit refresh")])
        self.assertEqual(screen.drawn, [
            "([{'id': 'recent closed items', 'status': 'open', 'title': 'recent closed items'}], {}) all_closed=False selected=recent closed items",
            "([{'id': 'all closed items', 'status': 'open', 'title': 'all closed items'}], {}) all_closed=True selected=all closed items",
        ])
        self.assertEqual(delays, [])

    def test_dashboard_navigation_selects_a_task_without_reading_again(self):
        snapshot = ([
            {"id": "bd-1", "status": "open", "title": "First"},
            {"id": "bd-2", "status": "open", "title": "Second"},
        ], {})
        screen, reader, delays = self.run_dashboard([ord("j"), ord("q")], [snapshot, "quit refresh"])
        self.assertEqual(reader.calls, [snapshot, "quit refresh"])
        self.assertEqual(screen.drawn, [
            "%s all_closed=False selected=bd-1" % (snapshot,),
            "%s all_closed=False selected=bd-2" % (snapshot,),
        ])
        self.assertEqual(delays, [])

    def test_dashboard_details_refresh_and_show_blockers_for_selected_task(self):
        initial = ([
            {"id": "bd-1", "status": "open", "title": "First"},
            {"id": "bd-2", "status": "open", "title": "Second"},
        ], {"bd-2": {"blocked_by": ["bd-9"]}})
        fresh = ([
            {"id": "bd-1", "status": "open", "title": "First"},
            {"id": "bd-2", "status": "open", "title": "Second", "description": "The complete description."},
        ], {"bd-2": {"blocked_by": ["bd-9"]}})
        screen, reader, delays = self.run_dashboard(
            [ord("j"), 10, 27, ord("q")], [initial, fresh, "quit refresh"]
        )
        self.assertEqual(reader.calls, [initial, fresh, "quit refresh"])
        self.assertIn("Task details: bd-2", screen.drawn[2])
        self.assertIn("The complete description.", screen.drawn[2])
        self.assertIn("- bd-9", screen.drawn[2])
        self.assertEqual(screen.drawn[3], "%s all_closed=False selected=bd-2" % (fresh,))
        self.assertEqual(delays, [])

    def test_dashboard_opens_the_selected_workers_log_from_the_table(self):
        initial = ([
            {"id": "bd-1", "status": "open", "title": "First"},
            {"id": "bd-2", "status": "open", "title": "Second"},
        ], {})
        fresh = ([
            {"id": "bd-1", "status": "open", "title": "First"},
            {"id": "bd-2", "status": "open", "title": "Second"},
        ], {})
        screen, reader, delays = self.run_dashboard(
            [ord("j"), ord("l"), 27, 27, ord("q")], [initial, fresh, "quit refresh"]
        )
        self.assertEqual(reader.calls, [initial, fresh, "quit refresh"])
        self.assertIn("Worker log: bd-2", screen.drawn[2])
        self.assertIn("log bd-2", screen.drawn[2])
        self.assertIn("Task details: bd-2", screen.drawn[3])
        self.assertEqual(screen.drawn[4], "%s all_closed=False selected=bd-2" % (fresh,))
        self.assertEqual(delays, [])

    def test_task_paths_are_under_common_state_directory(self):
        worktree, branch, log = asco.task_paths("/project", "bd-42")
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

    def test_all_ordinary_task_types_are_dispatchable(self):
        runner = asco.Runner("/project", 2)
        ready = [{"id": "bd-1", "issue_type": "bug"}, {"id": "bd-2", "issue_type": "task"},
                 {"id": "bd-3", "issue_type": "epic"}]
        self.assertEqual(runner.dispatchable_tasks(ready, ready), ready)

    def test_escalation_is_not_dispatchable(self):
        runner = asco.Runner("/project", 2)
        task = {"id": "bd-1", "issue_type": "task"}
        escalation = {"id": "bd-2", "issue_type": "escalation"}
        self.assertEqual(runner.dispatchable_tasks([task, escalation], [task, escalation]), [task])

    def test_only_one_integration_task_is_dispatchable(self):
        runner = asco.Runner("/project", 2)
        integration = {"id": "bd-1", "status": "open", "labels": ["asco:integration"]}
        active = {"id": "bd-2", "status": "in_progress", "labels": ["asco:integration"]}
        self.assertEqual(runner.dispatchable_tasks([integration], [integration, active]), [])

    def test_blocking_ids_handles_beads_dependency_records(self):
        self.assertEqual(asco.blocking_ids({"blocked_by": [{"depends_on_id": "bd-1"}, "bd-2"]}), ["bd-1", "bd-2"])

    def test_dependency_cycles_ignore_closed_tasks_and_find_a_cycle(self):
        issues = [
            {"id": "bd-1", "status": "open", "dependencies": [{"id": "bd-2", "dependency_type": "blocks"}]},
            {"id": "bd-2", "status": "open", "dependencies": [{"id": "bd-1", "dependency_type": "blocks"}]},
            {"id": "bd-3", "status": "closed", "dependencies": [{"id": "bd-1", "dependency_type": "blocks"}]},
        ]
        self.assertEqual(asco.dependency_cycles(issues), [("bd-1", "bd-2")])

    def test_parent_child_cycle_has_one_safe_repair(self):
        issues = [
            {"id": "parent", "status": "open", "dependencies": [
                {"id": "child-a", "dependency_type": "blocks"},
                {"id": "child-b", "dependency_type": "blocks"},
            ]},
            {"id": "child-a", "status": "open", "dependencies": [{"id": "parent", "dependency_type": "parent-child"}]},
            {"id": "child-b", "status": "open", "dependencies": [{"id": "parent", "dependency_type": "parent-child"}]},
        ]
        edges = asco.dependency_edges(issues)
        self.assertEqual(asco.dependency_cycles(issues, edges), [("child-a", "child-b", "parent")])
        self.assertEqual(asco.cycle_repair(("child-a", "child-b", "parent"), edges), [
            ("child-a", "parent", "parent-child"),
            ("child-b", "parent", "parent-child"),
        ])

    def test_runner_creates_repair_task_only_for_an_unambiguous_cycle(self):
        class FakeBeads:
            def __init__(self):
                self.calls = []
            def run(self, *args, **kwargs):
                self.calls.append(args)
        runner = asco.Runner("/project", 2)
        runner.bd = FakeBeads()
        issues = [
            {"id": "parent", "status": "open", "dependencies": [{"id": "child", "dependency_type": "blocks"}]},
            {"id": "child", "status": "open", "dependencies": [{"id": "parent", "dependency_type": "parent-child"}]},
        ]
        runner.resolve_dependency_cycles(issues)
        create = runner.bd.calls[0]
        self.assertEqual(create[:2], ("create", "Repair Beads dependency cycle: child, parent"))
        self.assertIn("--metadata", create)
        self.assertIn("asco_cycle_repair", create[create.index("--metadata") + 1])

    def test_runner_blocks_one_task_when_a_cycle_has_multiple_repairs(self):
        class FakeBeads:
            def __init__(self):
                self.calls = []
            def run(self, *args, **kwargs):
                self.calls.append(args)
            def comment(self, *args):
                self.calls.append(("comment",) + args)
        runner = asco.Runner("/project", 2)
        runner.bd = FakeBeads()
        issues = [
            {"id": "bd-1", "status": "open", "dependencies": [{"id": "bd-2", "dependency_type": "blocks"}]},
            {"id": "bd-2", "status": "open", "dependencies": [{"id": "bd-1", "dependency_type": "blocks"}]},
        ]
        runner.resolve_dependency_cycles(issues)
        self.assertEqual(runner.bd.calls[0][:4], ("update", "bd-1", "--status", "blocked"))
        self.assertIn("asco_needs_input=true", runner.bd.calls[0])
        self.assertIn("More than one safe repair exists.", runner.bd.calls[1][2])

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
    @patch.object(asco, "visible_issues", return_value=([{"id": "bd-1", "status": "blocked", "title": "Need a choice", "description": "Choose A or B.", "metadata": {"asco_needs_input": True}}], {}))
    @patch.object(asco, "process_alive", return_value=False)
    def test_status_places_waiting_task_above_task_table(self, alive, visible, dispatchers):
        report = asco.render_status("/project")
        self.assertIn("Tasks waiting for you:", report)
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

    def test_engineer_prompt_loads_the_audit_framework(self):
        prompt = asco.engineer_prompt({"id": "bd-2", "title": "Implement", "description": "Build it"},
                                      Path(__file__).parents[1], Path("/project/worktree"), "asco/task-bd-2",
                                      Path(__file__).parents[1] / ".asco/logs/bd-2.log")
        self.assertIn("Commit every repository change", prompt)
        self.assertIn("asco_needs_input=true", prompt)
        audit = asco.engineer_prompt({"id": "bd-3", "metadata": {"asco_prompt": "audit"}},
                                     Path(__file__).parents[1], Path("/project/worktree"), "asco/task-bd-3",
                                     Path(__file__).parents[1] / ".asco/logs/bd-3.log")
        self.assertIn("Audit framework", audit)

    @patch.object(asco, "process_alive", return_value=True)
    def test_closed_worker_still_counts_until_its_process_exits(self, alive):
        runner = asco.Runner("/project", 2)
        runner.bd.all = lambda: [{"id": "bd-1", "status": "closed", "metadata": {"asco_pid": "12"}}]
        self.assertEqual(runner.worker_count(), 1)

    def test_answer_reopens_the_task_waiting_for_input(self):
        class FakeBeads:
            def __init__(self, root):
                self.calls = []
            def show(self, issue):
                return {"status": "blocked", "metadata": {"asco_needs_input": True}}
            def comment(self, *args):
                self.calls.append(("comment",) + args)
            def run(self, *args):
                self.calls.append(("run",) + args)
        fake = FakeBeads("/project")
        with patch.object(asco, "Beads", return_value=fake):
            asco.answer_task("/project", "bd-2", "Retry it.")
        self.assertIn(("run", "update", "bd-2", "--status", "open", "--unset-metadata", "asco_needs_input"), fake.calls)

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
            runner.cleanup_closed_tasks(issues)
        command_call.assert_not_called()
        self.assertEqual(len(runner.bd.metadata), 2)
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
            runner.cleanup_closed_tasks(issues)
        command_call.assert_called_once_with(runner.root, ["git", "worktree", "remove", str(path)], check=False)
        self.assertEqual(len(runner.bd.metadata), 2)

    @patch.object(asco, "process_alive", return_value=False)
    def test_cleanup_refuses_an_out_of_scope_worktree(self, alive):
        runner, issues = self.cleanup_runner("/outside/bd-2")
        with patch.object(asco, "registered_worktrees", return_value={Path("/outside/bd-2")}), \
             patch.object(asco, "command") as command_call:
            runner.cleanup_closed_tasks(issues)
        command_call.assert_not_called()
        self.assertEqual(len(runner.bd.metadata), 1)


if __name__ == "__main__":
    unittest.main()
