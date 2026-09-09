import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("asco", Path(__file__).parents[1] / "src" / "asco.py")
asco = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(asco)


class AscoTests(unittest.TestCase):
    def test_task_paths_are_under_common_state_directory(self):
        worktree, branch, log = asco.task_paths("/project", "bd-42", "bd-1")
        self.assertEqual(worktree, Path("/project/.asco/worktrees/tasks/bd-42"))
        self.assertEqual(branch, "asco/task-bd-42")
        self.assertEqual(log, Path("/project/.asco/logs/bd-42.log"))

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

    def test_blocking_ids_handles_beads_dependency_records(self):
        self.assertEqual(asco.blocking_ids({"blocked_by": [{"depends_on_id": "bd-1"}, "bd-2"]}), ["bd-1", "bd-2"])

    def test_escalation_detection_uses_type_or_label(self):
        self.assertTrue(asco.is_escalation({"issue_type": "escalation"}))
        self.assertTrue(asco.is_escalation({"labels": ["escalation"]}))
        self.assertFalse(asco.is_escalation({"issue_type": "task"}))

    @patch.object(asco, "dispatcher_processes", return_value=[])
    @patch.object(asco, "visible_issues")
    @patch.object(asco, "process_alive", return_value=False)
    def test_status_uses_done_and_dependency_blocked(self, alive, visible, dispatchers):
        visible.return_value = ([
            {"id": "bd-1", "status": "open", "title": "Wait"},
            {"id": "bd-2", "status": "closed", "title": "Finished"},
        ], {"bd-1": {}})
        report = asco.render_status("/project")
        self.assertIn("dependency-blocked", report)
        self.assertIn("Done", report)
        self.assertIn("Assigned", report)

    @patch.object(asco, "dispatcher_processes", return_value=[])
    @patch.object(asco, "visible_issues", return_value=([{"id": "bd-1", "status": "open", "owner": "engineer-1"}], {}))
    @patch.object(asco, "process_alive", return_value=False)
    def test_status_reports_beads_assignment(self, alive, visible, dispatchers):
        self.assertIn("engineer-1", asco.render_status("/project"))

    @patch.object(asco, "dispatcher_processes", return_value=[])
    @patch.object(asco, "visible_issues", return_value=([{"id": "bd-1", "status": "blocked", "issue_type": "escalation", "title": "Need a choice", "description": "Choose A or B."}], {}))
    @patch.object(asco, "process_alive", return_value=False)
    def test_status_places_waiting_escalation_above_task_table(self, alive, visible, dispatchers):
        report = asco.render_status("/project")
        self.assertIn("Escalations waiting for you:", report)
        self.assertIn("Choose A or B.", report)

    @patch.object(asco, "dispatcher_processes", return_value=["4144  00:01 python asco.py _serve"])
    @patch.object(asco, "visible_issues", return_value=([], {}))
    def test_status_reports_dispatcher_process(self, visible, dispatchers):
        self.assertIn("Dispatcher: running: 4144", asco.render_status("/project"))

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


if __name__ == "__main__":
    unittest.main()
