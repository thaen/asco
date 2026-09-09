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
bd create "Title" --type task --description "Read file X and do what it says, or other detailed task description."
```

`asco` polls the ready queue and starts agents using `codex exec` with instructions to work on specific tasks, using the coordinator patterns from the Beads coordination document linked above.. It starts new tasks until there are X running tasks, then waits for one to finish before starting the next one.

Every ready ordinary Beads task is Engineer work, regardless of whether its type is `task`,
`bug`, `feature`, `chore`, or `epic`. Asco gives each task a branch and worktree based on the
default branch. Engineers commit their work before closing a task. An Engineer creates a normal
Integration task when a branch must be merged into the default branch; Asco runs one task labelled
`asco:integration` at a time, and that task's Engineer performs the merge and validation.

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

## Worker prompting: Task framework

`prompts/engineer.md` is the default, editable Engineer framework. It tells an Engineer to judge
scope, complete small tasks directly, and divide nontrivial work into focused Beads tasks when
separate Engineers can make progress. Parent relations group work; `blocks` dependencies express
order. A task that waits for children remains open and dependency-blocked until Beads makes it
ready again.

`prompts/audit.md` is an editable Audit framework selected by task metadata
`asco_prompt=audit`. An Audit task is ordinary Engineer work. It compares the original request,
delivered merge, implementation, and tests. A passing audit closes with a conclusion. An audit
that finds gaps files correction tasks and a successor Audit task that blocks on those corrections,
then closes with its findings. The successor repeats the audit after correction work closes.

The Engineer framework directs code work through ordinary Integration tasks labelled
`asco:integration`. Their Engineers merge named branches into the default branch and validate the
result. The dispatcher serializes those tasks but does not make merge decisions.

## Engineer communication and escalation

Engineers communicate between tasks by using comments on tasks and referencing task IDs when needed. For instance, when decomposing the original user-requested task, the Engineer may file the tasks in reverse order, so that it can add instructions "leave comments on the next tickets as you learn important things".

When a worker needs a user decision, it changes its own task to `blocked`, sets
`asco_needs_input=true`, and adds a comment with the question, options, and consequences. The UI
lists those tasks. `asco answer TASK_ID "answer"` adds the answer as a comment, clears the marker,
and reopens the same task. Separate escalation tasks and Beads gates are not used.

When an Engineer process exits while its task remains `in_progress`, Asco reopens the task and
retries it once. A second unfinished exit changes that same task to `blocked` with
`asco_needs_input=true` and a comment that names its worker log. An already closed task remains
complete when its process later exits.
