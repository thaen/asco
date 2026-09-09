ASCO, short for "A Software Company", is a system allowing a user to create tasks intended for AI agents to execute, view status on those agents' execution, and respond to escalations. I intend this to be like Yegge's Gastown, but with fewer moving parts and fewer (in my opinion) unnecessary opinions.

We are implementing several of the coordination patterns from https://github.com/gastownhall/beads/blob/main/docs/multi-agent/coordination.md, read that before beginning. 

## Implementation discretion

This document defines product behavior and workflow invariants. The Engineer may choose ordinary implementation details, including programming language, UI library, polling interval, log format, process-record format, and testing structure, when those choices preserve the behavior described here.

The Engineer must raise a question only when a choice would change user-visible behavior, task lifecycle semantics, data ownership, security boundaries, or the stated workflow.

## Usage: Task submission

Version one manages one Git repository. Start `asco` at that repository's root;
`asco` uses its Beads database and default branch. The `all-projects` directory
in this example is one repository, not a collection of repositories. `asco run`
is nonblocking:

```
mkdir all-projects
cd all-projects
asco run --parallel X
```

Users submit tasks by creating beads in the "ready" state in a specific directory:

```
cd all-projects
bd create "Title" --type epic --description "Read file X and do what it says, or other detailed task description."
```

`asco` polls the ready queue and starts agents using `codex exec` with instructions to work on specific tasks, using the coordinator patterns from the Beads coordination document linked above.. It starts new tasks until there are X running tasks, then waits for one to finish before starting the next one.

When Asco claims an Epic, it creates an Epic integration branch and worktree
from the default branch. Spawned agents have their own child worktrees and
branches based on that Epic integration branch. A child task that changes the
repository commits its work before it closes. Asco merges that work into the
Epic integration branch before it starts a dependent child task. Only one
Engineer updates a given Epic integration branch at a time.

When spawned, Beads is updated with metadata about the Engineer that is working on the task. The metadata is the durable process record: it has the Engineer identity, PID, start time, worker branch, worktree location, log location, and eventual exit status (for example, `--set-metadata pid=12345`). Asco writes worker standard output and standard error to `.asco/logs/<bead-id>.log` at the repository root. The log path stored in metadata is relative to that root.

## Usage: Status reporting

A UI allows a user to view the state of all Beads tasks and their status. The
built-in stored statuses are open, in_progress, blocked, deferred, closed,
pinned, and hooked. The UI shows dependency availability separately: an open
task with unresolved `blocks` dependencies is dependency-blocked, and its
blocking task IDs are shown. The UI calls tasks with stored status `closed`
Done, shows the most recently closed 10 first, and has an option to view all
closed tasks.

### Dashboard refresh boundaries

The dashboard has one cached Beads snapshot while it is idle. Its periodic
draw loop may repaint that snapshot and the dispatcher log, but it does not
read Beads again. Data can therefore be stale until the user begins an
operation.

The `c` key toggles the closed-item view and obtains a fresh Beads snapshot
before it draws the new view. The `q` key obtains a fresh Beads snapshot before
the dashboard exits. These operation-triggered reads are synchronous, so a key
operation has a defined data boundary. Escape remains a quit alias, but the
refresh requirement applies specifically to `q` and `c`.

The dashboard code has a controller seam that accepts a snapshot reader, a
renderer, a screen input/output adapter, and a delay or clock. A scripted fake
screen can provide keys and record draws, and a sequential fake reader can
provide snapshots and record reads. Tests must prove one initial read, no
additional reads during idle ticks, one fresh read for each `c`, the resulting
closed-item output, and one fresh read before `q` exits. The tests must not
need a terminal, real sleeps, or a Beads database.

It can be a Terminal UI built with Python, tested with Pyte and Pexpect, or it can be a WebUI with no back-end (TamperMonkey is OK if needed). The initial Engineer is empowered to make the implementation decision based on which UI is easier and faster to test, which I suspect is a Terminal UI.

## Beads interaction

Shelling out the `bd` tool should rarely be necessary for reads. We query Beads with SQL wherever possible via `bd` or directly against the database.

For writes, we should prefer shelling out to `bd` unless there exists a better interface. For instance, the `--claim` operation should probably be done with `bd`, not directly via the database.

## Worker prompting: Persona

To start with, there is only one persona: "Engineer". The Engineer is an expert software development engineer. As a tenured engineer, he understands that all software has tremendous cost in maintenance and testing. He minimizes the code he writes through judicious re-use of existing software.

The Engineer understands that they do not work alone. They escalate important decisions. They delegate tasks to other Engineers, such as test authoring, investigation and research, and more. They share their findings in the company Wiki. 

## Worker prompting: SDLC

Users submit Epics by creating Beads with type `epic`. Epics are considered to
be software engineering work.

When Asco claims an Epic, it starts an Engineer to break down the work. The
Epic becomes `in_progress` when it is claimed and remains `in_progress` while
its child tasks execute. An in-progress Epic represents work that is underway,
even when no Engineer is currently decomposing it.

The Engineer creates as many child tasks as the work requires in these
categories. Every child task uses `--parent EPIC_ID`.

1. High-level tests: Write a reasonable number of end-to-end tests for the requested feature. They should be failing when this task is completed.
2. Implementation: Implement the feature. The tests should be passing when this task is completed. 
3. Test: More thoroughly test the feature, especially focused on details, logic, and failure paths. The build should pass when this step is completed.
4. Audit: Compare the original task that the user submitted (its text is included in this task) and what was implemented. Make sure the build succeeds. Compare the tests to what the user requested. Tests should exist for the features that the user requested. If this step fails, file another task identical to this one, then file blocking correction tasks based on the audit results. The audit task is closed at this point, the output is potentially a separate audit task.
5. Merge: When finished, an Engineer is tasked with merging the Epic integration branch back to main, building, and running tests. Asco deletes the Epic and child worktrees after the Epic closes. Only one merge to main can happen at a time.

For a given Epic, child tasks use `blocks` dependencies to express their
required order. Implementation tasks must not start before their required
high-level test tasks have closed. Test tasks must not start before their
required implementation tasks have closed. The Merge task must wait for every
task whose work it integrates.

## SDLC: Child tasks

Child tasks are worked like typical engineering tasks: Optional but encouraged red/green TDD: test, Implement, repeat; then audit and merge the committed work back to the Epic integration branch. Ideally each of these is worked by a different Engineer and comments are added as they go. I want the "Audit" step to be the same as above, but doing this recursively forever is silly of course, not totally sure how to handle that yet, but let's try it first to see how it goes.

Child tasks do not block their Epic. The `parent-child` relationship keeps the
work grouped under the Epic, and Beads prevents the Epic from closing while it
has open child tasks.

Asco dispatches open Epics for decomposition and ready non-Epic tasks for
ordinary work. An in-progress Epic does not appear in the ready queue, while
its ready child tasks do appear.

After a child task closes, Asco checks its parent Epic. When every child task
has closed, including the Merge task, Asco closes that Epic by ID.

Engineers may create further tasks as they learn about the work. A task that
belongs to the current feature is created as another child of the current Epic
with any required `blocks` dependencies. An independent follow-up task uses a
`discovered-from` dependency. A large independent effort may be created as a
new Epic and follows the same lifecycle.

## Engineer communication and escalation

Engineers communicate between tasks by using comments on tasks and referencing task IDs when needed. For instance, when decomposing the original user-requested task, the Engineer may file the tasks in reverse order, so that it can add instructions "leave comments on the next tickets as you learn important things".

To escalate, a worker first changes its current task to `blocked`, then files a blocked task of type "escalation" under the current Epic. The escalation metadata records the blocked task ID, and its description states the question, options, and consequence of each option. The UI lists blocked escalations that wait for the user. `asco answer ESCALATION_ID "answer"` adds the answer as a comment, closes the escalation, and reopens its recorded blocked task for an Engineer retry.

When an Engineer process exits while its task remains `in_progress`, Asco blocks that task and files one linked escalation rather than automatically retrying it. An already closed task remains complete when its process later exits. Beads gates are not used for this workflow because their built-in human gate is a formula-step wait condition, while an escalation must retain a question, options, task comments, and a direct link to the task that waits for the answer.
