# How noisy one build's score is

The [improvement loop](https://github.com/existential-birds/code-review-benchmarks/issues/42)
keeps a change to Daydream only if it beats the baseline by more than chance. This
measures how much the score of one unchanged build moves from one run to the next on
the cases the loop scores, and derives from it how many rounds the loop runs and how
large a gain it accepts. The numbers are in [`loop/noise.json`](../loop/noise.json),
written by [`loop/noise.py`](../loop/noise.py); the settings it derives are in
[`loop/settings.toml`](../loop/settings.toml).

## What was measured

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

## The reviewer, round by round

| Round | Grounded P | Grounded R | Grounded F1 | Augmented P | Augmented R | Augmented F1 | Failed | Incomplete | Cost |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.947 | 0.173 | 0.293 | 0.973 | 0.329 | 0.491 | 2 | 0 | $123.52 |
| 2 | 0.926 | 0.182 | 0.304 | 0.964 | 0.338 | 0.500 | 2 | 0 | $124.69 |
| 3 | 0.936 | 0.173 | 0.293 | 0.967 | 0.324 | 0.485 | 2 | 0 | $129.69 |
| 4 | 0.941 | 0.191 | 0.317 | 0.973 | 0.336 | 0.499 | 2 | 0 | $124.43 |
| 5 | 0.917 | 0.176 | 0.295 | 0.958 | 0.328 | 0.489 | 2 | 0 | $125.63 |

Costs are API-equivalent estimates from the trials themselves, not subscription
charges: $627.96 for the 240 scored reviews, about $2.62 each. A round took two to
four hours.

## Spread between rounds

Five rounds estimate a standard deviation loosely, so each is also given at its
one-sided 80% upper bound, `s × √((n − 1) / χ²₀.₂₀(n − 1))`. With five rounds that
widens it by √(4 / 1.6488) = 1.558. The loop uses the upper bound.

| Pooled over | Grounded F1 SD (upper) | Grounded P | Grounded R | Augmented F1 | Augmented P | Augmented R |
|---|---|---|---|---|---|---|
| All 50 cases, and the 48 every round scored | 0.0104 (0.0162) | 0.0122 (0.0190) | 0.0074 (0.0115) | 0.0065 (0.0102) | 0.0064 (0.0100) | 0.0058 (0.0090) |
| The first 25 climb cases | 0.0189 (0.0294) | 0.0202 (0.0314) | 0.0132 (0.0206) | 0.0187 (0.0291) | 0.0098 (0.0153) | 0.0157 (0.0245) |

The judge adds almost nothing. Four oracle rounds scored every true finding as found;
in the fifth, one finding of `konturio_disaster-ninja-fe_1234` was judged not to match.
Over the five, grounded F1 has a mean of 0.9998 and an SD of 0.0005 (upper bound
0.0007), against the reviewer's 0.0104. Nearly all the noise is the reviewer's.

## The settings derived from it

The loop's target is a grounded F1 gain of `target_gain` = 0.02, accepted at
`accept_z` = 2 (an unchanged build passes about 1 time in 40), in at most
`rounds_max` = 5 rounds.

**`sigma` = 0.0162**, the upper-bound SD of pooled grounded F1 over the 50 cases:
0.0104 × 1.558.

**`accept_threshold(R) = accept_z × sigma × √(2 / R)`**, the gain two R-round means of
one build reach by chance about 1 time in 40:

| R | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| 50 cases | 0.0458 | 0.0324 | 0.0265 | 0.0229 | 0.0205 |
| 25 cases | 0.0833 | 0.0589 | 0.0481 | 0.0416 | 0.0372 |

**`rounds` = 5** and **`climb_set_size` = 50.** No R up to 5 brings either set's
threshold to 0.02. The 50-case set comes closest, at 0.0205 after five rounds, so the
loop uses it with the most rounds it allows.

**The guard margins** are each `accept_z` times the upper-bound SD of a difference of
two five-round means, `2 × sd × √(2/5)`:

- **`precision_margin` = 0.0241**, from the wider of the two precisions: grounded,
  2 × 0.0190 × 0.632.
- **`wrong_findings_margin` = 0.0264** findings per scored case. The judge found
  unmatched findings wrong in only 4 of the 240 reviews, one in each of four rounds.
  So rare a count can't show its spread in five rounds. Where its measured upper bound
  (0.0145) is below Poisson's, the square root of its mean count per round and at
  least one event (√1 / 48 = 0.0208), the margin uses Poisson's: 2 × 0.0208 × 0.632.
- **`incomplete_margin` = 1.79** unfinished reviews per round. No review ended
  incomplete, and the same two failed in every round, so the measured SD is zero.
  Poisson's, √2 = 1.414, gives 2 × 1.414 × 0.632. Once #68's catalogs score the two
  dotnet cases, the baseline's mean will be lower, and a margin derived again from it
  would be smaller.

**The canary**, for #47, is the first 10 climb cases. Its reference is the oracle's
mean of four counts per case (matched, matched true, unmatched true and golden
covered). No case among the 10 moved between oracle rounds, so its tolerance is the
floor of 1 finding.

## What the loop can and can't detect

The loop can detect a grounded F1 gain of 0.0205 or more on the 50 climb cases in five
rounds, about 7% of the baseline's 0.300. Its target, 0.02, is just out of reach. A
change worth 0.02 passes about half the time, and smaller ones less often still. A
25-case set would need a gain of 0.037. Halving the detectable gain would take four
times as many rounds, or roughly four times as many cases.

Scoring a candidate costs as much as the baseline: about $630 and 10 to 20 hours for
five rounds. A candidate that is clearly worse is stopped after its first or second
round.

The baseline is low in recall and high in precision: it covers about 18% of the
reference findings, and 93% of the findings it matches are true. Gains are likelier
to come from recall, while the precision guards keep a flood of findings from buying
it.

These five rounds are the loop's first baseline. Their jobs are `noise-45b-round-<N>-attempt-1`
and their summary is `artifacts/loop/noise-45b/summary.json`. The oracle rounds are
`oracle-45b`.
