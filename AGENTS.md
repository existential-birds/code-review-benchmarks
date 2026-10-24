# Agent guidance

## Never bypass Git hooks

Never use `--no-verify`, hook-disabling environment variables, or any other
mechanism that skips commit, push, or repository verification hooks. This is
never permitted, even when a failure appears unrelated or pre-existing. Fix
the failure on the current branch and retry the ordinary command with hooks
enabled, or stop and report the blocker. A commit or push is complete only
after the normal hook-enabled command succeeds.
