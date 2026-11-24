# Run the loop

The loop tunes Daydream on its own: each iteration proposes one change, scores it on
ReviewBench's dev cases against the pinned build, and keeps it only if it gains. A change
kept on dev is confirmed on the held-out split, outside the loop, and a confirmed change
waits for a person to merge it. Every step is recorded in the run's ledger,
`loop-runs/<run>/ledger.jsonl`. [`loop/run.py`](../loop/run.py) drives it; its `--help`
has the details.

## Before you start

- `make setup`, Docker running, and both logins (`codex login`, Pi's `/login`). The loop
  checks them with `make prereqs`, using the same login folders as the Makefile.
- The derived settings in `loop/settings.toml`'s `[derived]` table, from the noise
  measurement (`python -m loop.noise`, #45). The loop refuses to start without them.
- Nothing uncommitted: the run records the suite's commit.
- One loop per machine: a second one refuses to start while the first holds the lock.

## Start a run

```sh
.venv/bin/python -m loop.run --run <run> --baseline-name <name>
```

The run is `loop-runs/<run>/`; leave out `--run` for `run-<date>-<time>`. It first scores
the canary with the oracle; if the judge drifted, the run doesn't start. `--baseline-name`
names `loop.rounds` rounds of the pinned build on `loop/climb.txt`, as many as the derived
`rounds`. They are used while younger than `baseline_max_age_days`, by when their newest
round's Harbor job finished; otherwise, or without the option, the baseline is measured
again as `<run>-baseline`. A start that failed before its ledger existed can be run again
with the same name, and reuses the rounds it finished.

Then it repeats iterations until a stop rule holds. After a change is accepted on dev it
runs `loop.confirm` in a separate process and reads only its one word. A `confirmed`
change is a winner: with `auto_open_pr` on, its draft pull request would open, but that
is #57's, so for now the loop only says so. At the end it prints each iteration's verdict,
the best gain on dev, the spend and why it stopped.

## Why it stops

Each stop is a `stop` entry in the ledger, with a reason and a detail:

| Reason | When |
| --- | --- |
| `plateau` | `plateau_iterations` iterations in a row with nothing accepted on dev |
| `budget` | `run_max_iterations` iterations, `run_max_cost_usd_equiv` spent on them, or `run_max_wall_hours` since the run started |
| `held-out-regression` | a confirmation said `regressed` |
| `judge-drift` | the canary, run again every `canary_every` iterations, drifted |
| `winner-awaiting-merge` | `max_unmerged_winners` confirmed winners in the run |
| `held-out-worn` | `loop.confirm` refused: its held-out cases are used up (#58) |
| `infrastructure` | `invalid_streak_stop` invalid iterations in a row; a subscription limit still there after one wait of `limit_backoff_minutes`; a canary or confirmation that gave no verdict; or an error in the loop |

A subscription limit is Codex's "You've hit your usage limit" in an invalid iteration's
reasons, proposer logs or trial logs, or in a canary's or confirmation's error.

## Resume

```sh
.venv/bin/python -m loop.run --resume <run>
```

This continues from the ledger after a crash, Ctrl-C or a stop. It checks everything as at
the start and runs the canary again. An iteration with no entry, cut off by a crash or a
kill, is redone from its last finished round; one stopped by Ctrl-C is recorded invalid
and the next is proposed afresh. A change accepted on dev but not yet confirmed is
confirmed first.
After a stop, the plateau and invalid streaks count again from zero, but the budgets and
winners count the whole run: raise the setting to go on past one. After merging a winner,
start a new run, since the pin, and so the baseline, changed.

## Read the ledger

```sh
.venv/bin/python -m loop.ledger show <run>     # every entry, and each line's hash
.venv/bin/python -m loop.ledger verify <run>   # the chain is intact
.venv/bin/python -m loop.ledger ideas <run>    # one line per proposal, with its verdict
```

The run-start entry holds the settings the run used, the baseline and the canary's result.
Each iteration entry holds the proposal, the candidate commit (its patch is in
`loop-runs/<run>/patches/<n>.patch`), the gate, the climb- and check-set scores and the
cost. A confirmation holds one word; no held-out score is ever recorded. Each `--resume`
adds a line to `loop-runs/<run>/resumes.jsonl` with the settings it changed.

## Change a setting

Every setting and its reason is in [`loop/settings.toml`](../loop/settings.toml); change it
there and commit it. To change one for a single run, add `--set key=value`, typed as TOML,
once per setting:

```sh
.venv/bin/python -m loop.run --run trial --set auto_open_pr=false --set run_max_iterations=2
```

A run keeps the settings it started with. `--set` on `--resume` changes them for that
resume only, so repeat it on the next one; `run_max_wall_hours` counts from the run's
start, so a resume after a long pause may need it raised. `loop.confirm` reads
`loop/settings.toml` itself, so a run's `--set` doesn't reach the held-out rule.
