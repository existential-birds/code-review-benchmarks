# Agent guidance

Read [CONTRIBUTING.md](CONTRIBUTING.md) before making changes or working on a PR.
Tracked skills live in `.agents/skills/`, linked from `.claude/skills/`.

- **Never bypass Git hooks.** Don't use `--no-verify` or disable hooks, even for
  unrelated failures; fix the cause and retry normally, or report the blocker.
- **Fix first, then answer.** When asked for a fix, make and verify it before
  replying; don't stop to propose a plan or ask what the repo can answer.
- **Report whole-session status.** Before saying "done" or handing off, check the
  whole session: say "done" only when nothing remains open; otherwise list every
  unmerged PR, unfinished step and running job with what it needs.
- **Fix root causes upstream.** Fix defects in Daydream or other owner-controlled
  dependencies there; never mask them with workarounds here unless asked by name.
- **Preserve committed work and live runs.** Don't revert changes unasked; when a
  better fix lands, say so and let the owner decide. Let healthy live jobs finish
  (interrupted Harbor jobs can't resume), and use a worktree to edit alongside a
  live job or active branch.
