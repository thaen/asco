import unittest
from unittest.mock import Mock

from asco.beads import Beads
from asco.cli import submit_pair


class SubmitPairTests(unittest.TestCase):
    def test_submit_creates_engineering_and_dependent_audit(self):
        beads = Mock()
        beads.create.side_effect = ["asco-eng", "asco-audit"]

        engineering, audit = submit_pair(beads, "Task", "Objective", None, "2")

        self.assertEqual((engineering, audit), ("asco-eng", "asco-audit"))
        self.assertEqual(beads.create.call_args_list[0].args[2], "engineering")
        self.assertEqual(beads.create.call_args_list[1].args[2], "audit")
        self.assertEqual(beads.create.call_args_list[1].args[4], "asco-eng")
        beads.add_dependency.assert_not_called()


class ReadinessTests(unittest.TestCase):
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
