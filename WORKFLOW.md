# The company follows a small engineering workflow.

Every CEO request begins as an Intake task. The Engineer estimates its size, identifies risk, and breaks work into independently deliverable tasks before implementation starts.

Large or risky work receives a design task and an independent design review before implementation. Small work may proceed directly to implementation after the Engineer records why the work is small.

Implementation work changes one task branch, runs relevant checks, and opens a GitHub pull request. A fresh Engineer session performs review or QA when a task calls for it. The CEO merges pull requests.

Every engineering task has an Audit task. The Audit checks the stated objective and its evidence in the engineering worktree before merge. It either passes, escalates uncertainty, or files correction work as a child of the audited engineering task and then closes. A successor Audit is another child of that engineering task and depends on the correction; the successor audits the correction worktree. An Audit never depends on work that it created.

Every failure is blameless. The Engineer records facts, identifies the process or system condition that permitted the failure, and files corrective work that prevents, detects, or reduces recurrence.
