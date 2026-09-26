# DESIGN - Final design pass + demo polish (both people, last ~60 min before 16:00)

Depends on: P1 working end to end. Split: Person B's agent does the visual system; Person A does numbers, copy audit, rehearsal and video.

## Part 1 - Visual system (Person B's agent)
Follow this brief.

**Subject**: a clinic enrollment worker's morning queue, shown on a projector to judges, about people losing health insurance over paperwork. The feeling to hit: calm, exact, trustworthy - a tool that knows where every fact came from. Not a SaaS dashboard, not a startup landing page.

**Before touching code**, write a short plan in `web/DESIGN.md`:
- Palette: 4-6 named hex values. Bucket colors must carry meaning and stay distinguishable in grayscale (projectors wash out color; pair color with an icon or shape). SAFE should look quiet, ONE_AWAY should draw the eye. Avoid the generic defaults: cream + terracotta, black + acid green, card-grid with identical soft shadows.
- Type: one or two families chosen for this subject (dense tabular data + short human sentences). Tabular figures for all numbers. Projector scale: body >= 18px, counters very large.
- Source iconography: one consistent mark per `Source` type (billing code, note, patient's words, clinician, database). This is the product's signature - provenance visible everywhere. Spend the boldness here.
- Layout: ASCII wireframes for queue and case detail at 1440 and 1920 wide.
Then review the plan against this brief, revise anything generic, then build.

**Apply to**: queue, case detail, clinician card (should feel like a single calm form), buyer view, eval page, patient email HTML (plain, large text, single column, works in Gmail).

**Motion**: exactly one orchestrated moment - the counter reveal on "Run the state's check" / "Read the notes". Plus responsive motion on actions: span flash on claim click, the case-flip transition when Rosa's reply lands (show the bucket change clearly - it's the demo moment). Respect prefers-reduced-motion.

**Eval page (`/eval`)**: one chart comparing state method vs ours (recall is the headline, precision beside it), confusion matrices small below, verifier kept/dropped. Label "measured against labels we wrote on synthetic data".

**Quality floor**: visible keyboard focus, contrast AA, no horizontal scroll at 1440.

## Part 2 - Copy audit (Person A)
Grep the whole UI, email templates and PDF for, and fix:
- "eligible" / "not eligible" anywhere a patient could see it -> remove.
- "renewal application" -> "medical exemption attestation".
- "the state's patients" / "all patients in the state" -> "your clinic's patients".
- Buttons say what they do ("Email Rosa the question", "Sign attestation"), and the resulting event uses the same verb.
- "Synthetic data" visible on the queue header and PDF.
- Product name is **Lapse** everywhere: UI title, email sender name, PDF header, and `pitch.md` / `spec.md` (both still say "Still Covered").

## Part 3 - Numbers into the pitch (Person A)
Run `python -m engine.eval`, copy the real numbers into `pitch.md` sections 5 and the demo timeline in `spec.md` (replace 214 / 786 / 431 / 217 / 189). Check the arithmetic: final exempt - A exempt = "dropped who already qualify". Keep the California projections as three separate facts (4.8M subject / 2.2M expected exempt / 1.1M projected to lose coverage by 2029-30). Verify the "43 states" line against a source before saying it; drop it if you can't.

## Part 4 - Demo mode + backup (both)
- `make reset` -> `/api/demo/reset` -> queue at pre-reveal state. Add a hidden keyboard shortcut in the UI that does the same.
- Dry run the 3-minute script from `pitch.md` against the app three times. Fix whatever stalls.
- **Record the backup video by 16:00** (full demo, screen + voice), save to `demo/backup.mp4`. If anything breaks live, switch to it without apologizing.
- Offline check: turn Wi-Fi off, reset, run the demo. Everything except the real email send must still work from caches.

## Done when
Three clean rehearsals, video saved, numbers in the pitch match the screen exactly.
