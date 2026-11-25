# Agent guidance

Read and follow [CONTRIBUTING.md](CONTRIBUTING.md) before making changes or
creating or updating a PR. Repository skills live in `.agents/skills/` and are
linked into `.claude/skills/`.

## Never bypass Git hooks

Never use `--no-verify`, hook-disabling environment variables, or any other
mechanism that skips commit, push, or repository verification hooks. This is
never permitted, even when a failure appears unrelated or pre-existing. Fix
the failure on the current branch and retry the ordinary command with hooks
enabled, or stop and report the blocker. A commit or push is complete only
after the normal hook-enabled command succeeds.

## Fix first, then answer

When the owner asks for a fix, make the fix, verify it, and merge or apply it
before replying. Don't reply with questions or a plan in its place; ask only
when the fix truly can't proceed without the owner's decision.

## Status reports are final

Before saying a task is done or handing it off, list every open item: unmerged
pull requests, unfinished steps, and anything still running. Say "done" only
when that list is empty; otherwise the status names each open item and what it
needs. Never add a step to a task already reported as done unless the owner asks
a new question.

## Fix problems where they live

When a failure's cause is in Daydream or another dependency the owner controls,
fix it there. Add no workaround in this repository unless the owner asks for one
by name.

## Don't undo or stop work unasked

Never revert a change unless the owner asks; when a better fix lands, say so and
let the owner decide. Never stop a running live job unless it is failing or the
owner says to: let it finish and fix forward.

## Ask the deciding question early

When a decision is the owner's, ask it as soon as it is known, in one line with a
recommendation, before investigating further.

For long tasks and live runs, also follow
[Working on live runs and long tasks](CONTRIBUTING.md#working-on-live-runs-and-long-tasks).
