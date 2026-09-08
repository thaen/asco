import unittest
from unittest.mock import Mock

from asco.beads import Beads


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
