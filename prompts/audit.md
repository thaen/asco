# Audit framework

Read the original request, the named delivered merge, relevant task comments, implementation, and
tests. Compare what was asked for with what exists. Verify that tests cover the requested behavior
and meaningful failure paths, and verify that the implementation matches the request.

If the work passes, add a concise audit conclusion and close this task. If gaps exist, file focused
correction tasks that describe each gap. Then file a successor Audit task with the same original
request, delivered scope, and `asco_prompt=audit`. Add each correction task as a `blocks`
dependency of the successor Audit task, then close this task with its findings and the successor ID.

The successor Audit task runs only after its corrections close. This gives each audit pass a fixed
record while allowing audit and correction work to repeat until the work passes.
