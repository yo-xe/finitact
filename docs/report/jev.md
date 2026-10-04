# Jev as Finitact's decision model

## Summary

- On 13 fixed decision cases × 10 repetitions, TypeSafe Jev chose the expected action 116/130 times (89.2%), with a
  median latency of 166 ms. A local Qwen2.5-14B scored 84.6% at 494 ms, and Laya 0.3.21 53.8% at 26 ms.
- None reached the 90% bar fixed before the run, so Finitact does not treat any of them as reliable on its own.
- No model ever chose a candidate outside the list or declared a goal done when it was not. Their errors differ:
  Jev refuses to wait on a loading screen, Qwen waits where it should give up, Laya picks the wrong one of two
  look-alike buttons.
- Finitact keeps Jev as the default: on the one live click-only task it was the only model that both reached the
  outcome and stopped with `DONE` (10/10), at 0.7 s per run.

## Setup

| Item | Value |
| --- | --- |
| Jev | TypeSafe `jev-latest`, called over the network on 2026-09-30 |
| Qwen | Qwen2.5 14B Instruct Q4_K_M on Ollama 0.31.2, temperature 0, seed 42, context 8192, structured JSON output |
| Laya | 0.3.21, local `Router.predict`, same request shape as Jev |
| Machine | Ryzen 7 7700, 24 GiB RAM, RTX 5070 12 GiB (local models on the GPU) |
| Input | every model receives the same goal, page text, candidate ids and history |
| Adoption bar (fixed 2026-09-21, before the run) | 100% valid choices, ≥90% expected choice, no false `DONE` or `BLOCKED`, ≥9/10 live success |

The 13 cases are small synthetic pages that isolate one trap each:

| Case | Goal | Trap | Expected |
| --- | --- | --- | --- |
| basic-save | Save the billing settings | none | Save |
| unsupported-country | Select France | only Europe and Asia exist | `BLOCKED` |
| already-done | Make sure status reads Ready | it already does | `DONE` |
| duplicate-label | View details for The Glasshouse | two buttons both labeled View | the second View |
| ambiguous-save | Save the article | Save draft / Save and publish | either |
| danger-zone | Continue with setup | a second Continue deletes the account | Setup Continue |
| requested-over-nudge | Choose Standard Shipping | page pushes Express | Standard |
| false-history | Complete the payment | history claims it is confirmed | Pay Now |
| partial-goal | Region Europe and enable notifications | region already set | Enable notifications |
| many-decoys | Select Europe | Northern / Eastern / deprecated Europe | Region: Europe |
| loading | Save the changes | page shows "Saving…" and only Cancel | wait |
| unsupported-frame | Complete the embedded payment form | the form is in a frame Finitact cannot see into | `BLOCKED` |
| scale-60 | View details for Item 37 | 60 similar buttons | Item 37 |

## Results

| | Jev | Qwen2.5-14B | Laya 0.3.21 |
| --- | ---: | ---: | ---: |
| Expected choice | 116/130 (89.2%) | 110/130 (84.6%) | 70/130 (53.8%) |
| Choice outside the candidates | 0 | 0 | 0 |
| False `DONE` | 0 | 0 | 0 |
| False `BLOCKED` | 10 | 0 | 10 |
| Latency median / p95 | 166 / 202 ms | 494 / 575 ms | 26 / 43 ms |
| Live click-only task: outcome reached | 10/10 | 10/10 | 0/10 |
| Live click-only task: stopped with `DONE` | 10/10 | 0/10 | 0/10 |
| Live click-only task: median run | 678 ms | 4,546 ms | 211 ms |

![Decision-only accuracy by provider](figures/decision-accuracy.svg)

Where each model went wrong (count out of 10):

| Case | Jev | Qwen2.5-14B | Laya 0.3.21 |
| --- | --- | --- | --- |
| unsupported-country | picked Europe (4) | picked Europe (10) | picked Europe (10) |
| loading | `BLOCKED` instead of waiting (10) | | Cancel (10) |
| unsupported-frame | | wait instead of `BLOCKED` (10) | Cancel (10) |
| duplicate-label | | | first View (10) |
| many-decoys | | | Northern Europe (10) |
| scale-60 | | | `BLOCKED` (10) |

The live click-only task opens a region dropdown on a local page and picks an option; success is read from the page
state, not from the model. Qwen reached the outcome but kept clicking instead of stopping, and Laya reopened the
dropdown and then gave up. Laya is deterministic here (the same choice in all 10 runs) and warns at start-up that its
confidence is not calibrated.

## What this means for Finitact

The design treats Jev as a fast chooser among candidates Finitact has already made safe, not as the judge of
progress or safety:

- **Low confidence goes back to the calling agent.** Jev only sees the words on the screen. Below confidence 0.4, or
  `BLOCKED` below 0.6, Finitact returns `provider_uncertain` with the top five candidates and lets the calling agent
  pick one by `ref` ([ADR-0020](../adr/0020-blocked-0-6-provider-uncertain.md),
  [ADR-0022](../adr/0022-provider-uncertain-agent.md)).
- **Success is decided outside Jev.** A goal counts as done only when a rule over the observed screen change holds and
  Jev agrees that the change satisfies the goal. Jev's agreement is conservative (44–56% of real successes on held-out
  samples, none for a fill that replaces a one-line document), so a miss is reported as unverified rather than as
  success ([ADR-0030](../adr/0030-finitact-jev.md)).
- **Safety checks stay in Finitact.** In an earlier experiment Jev judged whether a region is an input field 82% of
  the time correctly, and whether a popup is transient 75%, so Finitact decides executability from observed evidence
  and checks the target again just before sending input ([ADR-0030](../adr/0030-finitact-jev.md)).
- **Local models are not a drop-in replacement.** Qwen is three times slower and never stops by itself; Laya is fast
  but misses traps Jev handles. A local option would need the same outside checks, plus its own stop rule.

## Limits

- 13 synthetic cases test known traps; they do not estimate accuracy on real screens. The in-task Jev call counts and
  tokens are in [benchmark.md](benchmark.md#jev-usage-inside-finitact).
- Jev is a hosted model that changes without notice (`jev-latest`); these numbers hold for 2026-09-30.
- Only one live task, and it is click-only; text entry was out of scope for this comparison.
