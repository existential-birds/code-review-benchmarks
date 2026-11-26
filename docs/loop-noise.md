# How noisy one build's score is

The [improvement loop](https://github.com/existential-birds/code-review-benchmarks/issues/42)
keeps a change to Daydream only if it beats the baseline by more than chance. This
measures how much the score of one unchanged build moves from one run to the next on
the cases the loop scores, and derives from it how many rounds the loop runs and how
large a gain it accepts. The numbers are in [`loop/noise.json`](../loop/noise.json),
written by [`loop/noise.py`](../loop/noise.py); the settings it derives are in
[`loop/settings.toml`](../loop/settings.toml).

## What was measured

- **Build:** Daydream 0.38.1 (`ca1b35e18ecab8e6e4e5b6c1dad968c23ceb14af`,
  `existential-birds/daydream#1983`), the pinned build.
- **Reviewer slot:** `harness = "daydream"`, `backend = "pi"`, `provider = "openrouter"`,
  `model = "deepseek/deepseek-v4.1-flash-1017"`, `effort = "default"`.
- **Cases:** the loop's climb set, 50 pull requests from ReviewBench's dev split,
  listed in [`loop/climb.txt`](../loop/climb.txt). No held-out case was scored.
- **Judge slot:** `harness = "pi"`, `provider = "openai"`, `model = "gpt-6.1-sol"`,
  `effort = "default"` (recorded as `openai/gpt-6.1-sol`), through Pi's ChatGPT login,
  as for every run of this suite.
- **Rounds:** five reviewer rounds, each a Harbor job of one trial per case,
  `-k 1 -r 1 -n 2` (`trial_retries = 1` retained from `802268f`, `capture_data: true`),
  scored with ReviewBench's own metric. Reused the five `oracle-45b` rounds from `#45`
  for the judge (identical `reviewbench_revision` `4f04392d6bf4941ab6f8a3ea0c15b449f4458864`,
  `split = "dev"`, `judge = "openai/gpt-6.1-sol"`, and 50 climb cases).

Rounds 1–3 scored all 50 cases. One trial ended `incomplete` (`ValueError: Review did
not complete analysis`) in Round 4 (`SpanPanel_span_207-212c534d`) and one in Round 5
(`agent-sh_agentsys_298-a5224fae`), so Rounds 4 and 5 scored 49 cases each and 48 cases
were scored in all five rounds (`matched`). On Round 5, `noise-82-ca1b35e-round-5-attempt-1`
failed with 31 errored trials after hitting the OpenRouter monthly spend limit; after the
limit was raised, `noise-82-ca1b35e-round-5-attempt-2` completed 42 trials before its
host process died abruptly and was resumed once via `harbor jobs resume` for its final
8 trials at the owner's direction.

## The reviewer, round by round

| Round | Job | Grounded P | Grounded R | Grounded F1 | Augmented P | Augmented R | Augmented F1 | Failed | Incomplete | Cost |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `noise-82-ca1b35e-round-1-attempt-1` | 0.871 | 0.308 | 0.455 | 0.885 | 0.531 | 0.664 | 0 | 0 | $9.82 |
| 2 | `noise-82-ca1b35e-round-2-attempt-1` | 0.888 | 0.312 | 0.462 | 0.895 | 0.530 | 0.666 | 0 | 0 | $9.03 |
| 3 | `noise-82-ca1b35e-round-3-attempt-1` | 0.891 | 0.317 | 0.467 | 0.910 | 0.528 | 0.668 | 0 | 0 | $9.63 |
| 4 | `noise-82-ca1b35e-round-4-attempt-1` | 0.926 | 0.279 | 0.428 | 0.881 | 0.459 | 0.603 | 0 | 1 | $9.66 |
| 5 | `noise-82-ca1b35e-round-5-attempt-2` | 0.928 | 0.344 | 0.502 | 0.921 | 0.547 | 0.687 | 0 | 1 | $9.64 |

The five finished rounds sum to **$47.78** (`$47.7775194545`) across 250 trials, about
`$0.19` per review; a round took about 3.5 to 4.2 hours of wall time. Including the failed
`noise-82-ca1b35e-round-5-attempt-1` (`$5.0143833739`, 50 trials) and the 2 pre-resume
interrupted trials of `noise-82-ca1b35e-round-5-attempt-2` (`s205109_AHKFlowApp_35-9ec13217`
`$0.0954737567` and `scorbo2_ext-sn-statistics_20-12f` `$0.1367764195`, `$0.2322501762`
subtotal), total spend across all `noise-82-ca1b35e` attempts was **$53.02** (`$53.0241530046`).

## Spread between rounds

Five rounds estimate a standard deviation loosely, so each is also given at its
one-sided 80% upper bound, `s × √((n − 1) / χ²₀.₂₀(n − 1))`. With five rounds that
widens it by √(4 / 1.6488) = 1.558. The loop uses the upper bound.

| Pooled over | Grounded F1 SD (upper) | Grounded P | Grounded R | Augmented F1 | Augmented P | Augmented R |
|---|---|---|---|---|---|---|
| All 50 cases | 0.0264 (0.0412) | 0.0249 (0.0388) | 0.0233 (0.0363) | 0.0316 (0.0493) | 0.0170 (0.0264) | 0.0345 (0.0538) |
| The 48 cases every round scored | 0.0284 (0.0442) | 0.0249 (0.0388) | 0.0249 (0.0389) | 0.0186 (0.0290) | 0.0123 (0.0192) | 0.0189 (0.0294) |
| The first 25 climb cases | 0.0421 (0.0656) | 0.0139 (0.0216) | 0.0350 (0.0545) | 0.0324 (0.0504) | 0.0208 (0.0324) | 0.0317 (0.0494) |

The judge (`oracle-45b`) adds almost nothing. Every oracle round scored all 50 cases,
with no failed or incomplete trial, and every reference finding was covered in every
round, so recall was 1 throughout. Only one case scored differently between rounds: in
round 3, one submitted finding of `konturio_disaster-ninja-fe_1234` was matched without
counting as a true match, 1 of the 477 matched that round.

| Oracle | Grounded P | Grounded R | Grounded F1 | Augmented P | Augmented R | Augmented F1 |
|---|---|---|---|---|---|---|
| Rounds 1, 2, 4 and 5 | 1 | 1 | 1 | 1 | 1 | 1 |
| Round 3 | 0.9979 | 1 | 0.9990 | 0.9979 | 1 | 0.9990 |
| SD (upper) | 0.0009 (0.0015) | 0 (0) | 0.0005 (0.0007) | 0.0009 (0.0015) | 0 (0) | 0.0005 (0.0007) |

Against the reviewer's grounded F1 SD of 0.0264, the judge's 0.0005 is negligible:
nearly all the noise is the reviewer's.

## The settings derived from it

The loop's target is a grounded F1 gain of `target_gain` = 0.02, accepted at
`accept_z` = 2 (an unchanged build passes about 1 time in 40), in at most
`rounds_max` = 5 rounds.

**`sigma` = 0.0412** (`0.041192679604331296`), the upper-bound SD of pooled grounded F1
over the 50 cases: 0.0264 × 1.558.

**`accept_threshold(R) = accept_z × sigma × √(2 / R)`**, the gain two R-round means of
one build reach by chance about 1 time in 40:

| R | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| 50 cases | 0.1165 | 0.0824 | 0.0673 | 0.0583 | 0.0521 |
| 25 cases | 0.1856 | 0.1312 | 0.1071 | 0.0928 | 0.0830 |

**`rounds` = 5** and **`climb_set_size` = null** (`loop/settings.toml` keeps 50 cases).
No R up to 5 brings either set's threshold to 0.02. The 50-case set comes closest, at
`smallest_detectable_gain` = **0.0521** (`0.0521050761901002`) after five rounds, so the
loop uses all 50 cases with the most rounds it allows.

**The guard margins** are each `accept_z` times the upper-bound SD of a difference of
two five-round means, `2 × sd × √(2/5)`:

- **`precision_margin` = 0.0491** (`0.0491416137315116`), from the wider of the two
  precisions: grounded, `2 × 0.038850 × 0.632456`.
- **`wrong_findings_margin` = 0.1740** (`0.17401214759988276`) findings per scored case.
  Across 248 scored reviews, 116 unmatched findings were judged wrong (mean `0.4678` per
  case, SD `0.0883`, upper bound `0.1376`, above Poisson's `0.0970`):
  `2 × 0.137569 × 0.632456`.
- **`incomplete_margin` = 1.2649** (`1.2649110640673518`) unfinished reviews per round.
  Two reviews ended incomplete across the five rounds (mean `0.40`, SD `0.5477`, upper
  bound `0.8531`), which is below Poisson's floor of `√1 = 1.0`, so `2 × 1.0 × 0.632456`.

**The canary**, for #47, is the first 10 climb cases. Its reference is the oracle's
mean of four counts per case (matched, matched true, unmatched true and golden
covered). No case among the 10 moved between oracle rounds, so its tolerance is the
floor of 1 finding.

## What the loop can and can't detect

The loop can detect a grounded F1 gain of **0.0521** or more on the 50 climb cases in
five rounds, about 11% of the baseline's 0.463 mean grounded F1. Its target, 0.02, is
out of reach within five rounds on 50 cases (and a 25-case set would need 0.0830).

Scoring a candidate costs about as much as the baseline: about $48 and 18 to 21 hours
for five rounds (~$0.19 per review). A candidate that is clearly worse is stopped after
its first or second round.

The baseline averages 31.2% grounded recall (51.9% augmented recall) and 90.1% grounded
precision (89.8% augmented precision), with 0.468 unmatched wrong findings per scored
case. Gains are still likeliest to come from recall, while the precision guards keep a
flood of findings from buying it.

These five rounds are the #82 baseline under the Pi + DeepSeek V4.1 Flash slot. Their
jobs are `noise-82-ca1b35e-round-1-attempt-1`, `noise-82-ca1b35e-round-2-attempt-1`,
`noise-82-ca1b35e-round-3-attempt-1`, `noise-82-ca1b35e-round-4-attempt-1`, and
`noise-82-ca1b35e-round-5-attempt-2` (after failed `noise-82-ca1b35e-round-5-attempt-1`),
and their summary is `artifacts/loop/noise-82-ca1b35e/summary.json`
(SHA-256 `d325431b10098882058e67915af23e514b7c3955fadf8427d0687bb9fac4f78b`). The oracle
rounds are `oracle-45b`, summarised in `artifacts/loop/oracle-45b/summary.json`
(SHA-256 `71562b4df48e0d89682e5e954217bc2cbf3acfa599bcdaeeece3c1015ef64c3e`).
`loop/noise.json` is `.venv/bin/python -m loop.noise` on the two.

---

## History: #45 (Daydream 0.37.0 on Codex)

### What was measured

- **Build:** Daydream 0.37.0 (`f18f4f0`), the pinned build, with its default review
  profile, reviewing through Codex on a ChatGPT subscription.
- **Cases:** the loop's climb set, 50 pull requests from ReviewBench's dev split,
  listed in [`loop/climb.txt`](../loop/climb.txt). No held-out case was scored.
- **Judge:** `gpt-6.1-sol` through Pi's ChatGPT login, as for every run of this suite.
- **Rounds:** five reviewer rounds, each a fresh Harbor job of one trial per case,
  `-n 2`, no retries, scored with ReviewBench's own metric. Then five rounds of the
  oracle, which submits each case's own true findings, so any spread between them is
  the judge's alone.

Two cases, `dotnet_sdk_53693` and `dotnet_sdk_53906`, failed in every round. The
review finished, but the suite's pinned comparison didn't match the bytes Daydream
reviewed, because the checkout converted line endings in 96 `.sln` files. #68 fixed
that after these catalogs were built. Every round therefore scored the same 48 cases,
and the figures on the cases every round scored are the same as on all 50.

### The reviewer, round by round

| Round | Grounded P | Grounded R | Grounded F1 | Augmented P | Augmented R | Augmented F1 | Failed | Incomplete | Cost |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.947 | 0.173 | 0.293 | 0.973 | 0.329 | 0.491 | 2 | 0 | $123.52 |
| 2 | 0.926 | 0.182 | 0.304 | 0.964 | 0.338 | 0.500 | 2 | 0 | $124.69 |
| 3 | 0.936 | 0.173 | 0.293 | 0.967 | 0.324 | 0.485 | 2 | 0 | $129.69 |
| 4 | 0.941 | 0.191 | 0.317 | 0.973 | 0.336 | 0.499 | 2 | 0 | $124.43 |
| 5 | 0.917 | 0.176 | 0.295 | 0.958 | 0.328 | 0.489 | 2 | 0 | $125.63 |

Costs are API-equivalent estimates from the trials themselves, not subscription
charges: $627.96 for all 250 reviews, the failed ones included, about $2.51 each. A
round took two to four hours.

### Spread between rounds

| Pooled over | Grounded F1 SD (upper) | Grounded P | Grounded R | Augmented F1 | Augmented P | Augmented R |
|---|---|---|---|---|---|---|
| All 50 cases, and the 48 every round scored | 0.0104 (0.0162) | 0.0122 (0.0190) | 0.0074 (0.0115) | 0.0065 (0.0102) | 0.0064 (0.0100) | 0.0058 (0.0090) |
| The first 25 climb cases | 0.0189 (0.0294) | 0.0202 (0.0314) | 0.0132 (0.0206) | 0.0187 (0.0291) | 0.0098 (0.0153) | 0.0157 (0.0245) |

### The settings derived from it

- **`sigma` = 0.0162**, `rounds` = 5, `climb_set_size` = 50, `accept_threshold(5)` = 0.0205 (25-case threshold 0.0372).
- **`precision_margin` = 0.0241**, **`wrong_findings_margin` = 0.0264**, **`incomplete_margin` = 1.79**.

These five rounds were the loop's first baseline. Their jobs are
`noise-45b-round-<N>-attempt-1` and their summary is
`artifacts/loop/noise-45b/summary.json` (SHA-256 `9cfca9e3…cb2a`). The oracle rounds
are `oracle-45b`, summarised in `artifacts/loop/oracle-45b/summary.json`
(`71562b4d…4c3e`).
