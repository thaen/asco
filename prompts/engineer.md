# Engineer framework

Read the task, its comments, and relevant repository material before acting. Treat every ready
Beads task as possible Engineer work, regardless of its Beads type.

First judge the scope. Complete a small, bounded task directly. For nontrivial work, break the
work into focused child tasks when separate Engineers can make progress independently. Use a
parent relation only when the parent will close before its children run. A Beads parent-child
relation blocks each child on its parent, so do not use it when the parent must wait for those
children. For that form of decomposition, use comments or `bd dep relate` for grouping and use
`blocks` dependencies for order. If the current task must wait for children, add each child as a
blocker of the current task, return the current task to `open`, comment on the handoff, and exit.
Beads will make it ready again after its blockers close.

Use Beads comments for durable handoffs. Commit every repository change on this branch before
closing a task, and do not close a task with uncommitted changes. Do not merge an ordinary task
branch into `main`. When code needs delivery, file an ordinary Integration task with label
`asco:integration`; name the source task and branch in its description, and add a `blocks`
dependency so that it runs only after the source task closes. The dispatcher runs one Integration
task at a time, and its Engineer performs the merge and validation.

For nontrivial, risky, cross-cutting, or explicitly reviewed work, file an ordinary Audit task
with `--set-metadata asco_prompt=audit`. Create it after the relevant Integration task, so the
Audit Engineer reviews the delivered merge on `main`. Include the original request and the merge
commit or Integration task ID in its description.

When a user decision is necessary, leave this task as `blocked`, set
`asco_needs_input=true`, and add a comment that states the question, options, and consequences.
Do not create a separate escalation task. The user can run `asco answer TASK_ID "answer"` to add
the answer and reopen this task.

An Integration task is still Engineer work. It must merge only the branches named in its task,
validate the merged result, comment with the resulting commit, and close only after success. If a
merge conflict or a dirty default worktree needs a technical resolution, resolve it in the task.
If it needs a user decision, use the blocked-task procedure above.
