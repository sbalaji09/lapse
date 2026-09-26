# LAPSE - build folder

Healthcare AI Hackathon, Sat 2026-09-26. Two people, each driving their own coding agents.

## Files
| File | Who reads it |
|---|---|
| `PROJECT.md` | Every agent, first. What we're building, the domain rules, shared schemas, API, fixtures. |
| `TRACK_A.md` | Person A's agents - engines and data |
| `TRACK_B.md` | Person B's agents - API, UI, patient loop |
| `DESIGN.md` | Both, at the end - visual pass, copy audit, rehearsal, backup video |
| `AWS.md` | After the hackathon - moving each local piece onto AWS services, section by section |

Paste into each agent: "Read build/PROJECT.md, then do section <X> of build/TRACK_<A|B>.md. Do only that section. Don't edit files the other track owns. If a contract in PROJECT.md needs to change, stop and tell me."

## Order
```
Person A:  T0a -> A1 -> A2 + A3 (parallel) -> A4 -> A5 -> A6 (stretch)
Person B:  T0b -> B1 -> B2 + B3 + B4 (parallel) -> B5 (stretch)
Both:      DESIGN.md
```
Track B builds on `fixtures/` (7 golden patients from T0a). Track A's real data replaces them at 13:30 with no code change on B's side.

## Checkpoints
| Time | Must be true |
|---|---|
| +20 min | Shared models + golden fixtures merged; api and web boot |
| 13:30 (P0) | Both channels over full cohort, buckets, solver, queue + case detail with clickable spans. Integration. |
| 15:00 (P1) | Rosa end to end: email out, reply in, case flips, clinician signs, PDF out |
| 16:00 (P2) | Verifier badge, accuracy chart, fragile list. **Backup video recorded.** |
| after (P3) | Voice clip, Nebraska pack, appeal packet |

Kill criteria: if at 13:30 both channels aren't producing determinations over the cohort, drop to 50 patients, cut verifier, voice and appeal, and demo the loop on Rosa alone.

## Rules for every agent
- Synthetic data only. No PHI.
- Never tell a patient they are or aren't eligible.
- Never show a claim without a clickable source span.
- Cache every LLM call to disk. Demo must replay offline.
- Pitch numbers (214 / 431 / 217 / 189) are placeholders until the eval runs.
