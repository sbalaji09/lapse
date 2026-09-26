// Verification scenarios for the /tests page. Each one re-runs a claim the product makes against the live API
// and reports every check as Expected vs Observed, in plain language. Scenarios that change data start from the
// original sample data, so each one can run on its own.

import { BUCKET_LABEL, STATUS_LABEL } from "@/components/labels";

export type Tone = "sky" | "sun" | "clay" | "stone" | "sage";

export interface Check {
  name: string;
  ok: boolean;
  expected?: string;
  observed?: string;
}

export type GroupId = "figures" | "queue" | "journeys" | "safeguards";

export interface Scenario {
  id: string;
  group: GroupId;
  title: string;
  verifies: string;
  matters: string;
  tone: Tone;
  mutates: boolean;
  run: () => Promise<Check[]>;
}

export const GROUPS: Array<{ id: GroupId; title: string; intro: string }> = [
  { id: "figures", title: "Figures", intro: "The numbers shown to a clinic are the numbers the accuracy run measured." },
  { id: "queue", title: "Work queue", intro: "The list an enrollment worker opens every morning." },
  { id: "journeys", title: "Member journeys", intro: "What happens to individual sample members, end to end." },
  { id: "safeguards", title: "Safeguards", intro: "Rules the product must never break." },
];

// ---------------------------------------------------------------------------------------------
// Plain-language formatting of whatever the API returns

const WORDS: Record<string, string> = {
  ...BUCKET_LABEL,
  ...STATUS_LABEL,
  not_determined: "Cannot determine",
  exempt: "Exempt",
  compliant: "Meets the requirement",
  standing_tolerance_minutes: "How long they can stand",
  enrolled_half_time_school: "School enrollment",
  limitation_attested: "Clinician attestation",
  patient: "The member",
  database: "A database",
  clinician: "The clinician",
  medically_frail: "Medical frailty",
  patient_reply: "The member's own words",
  note_span: "A clinical note",
};

export function plain(v: unknown): string {
  if (v === true) return "Yes";
  if (v === false) return "No";
  if (v === null || v === undefined) return "None";
  if (typeof v === "number") return v.toLocaleString();
  if (Array.isArray(v)) return v.map(plain).join(", ");
  if (typeof v === "string") return WORDS[v] ?? v;
  return JSON.stringify(v);
}

class Checks {
  list: Check[] = [];
  /** A check whose expected outcome is described in words. */
  that(name: string, ok: boolean, expected: string, observed?: string) {
    this.list.push({ name, ok, expected, observed: observed ?? (ok ? expected : "Not as expected") });
  }
  /** A check comparing a value with the value it must equal. */
  eq(name: string, actual: unknown, expected: unknown) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    this.list.push({ name, ok, expected: plain(expected), observed: plain(actual) });
  }
}

// ---------------------------------------------------------------------------------------------
// API helpers

async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  if (!res.ok) throw new Error(`The system returned an error (${res.status}) for ${init?.method ?? "GET"} ${path}`);
  return res.json();
}

const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });

function describeSpans(spans: Array<{ source: string }>): string {
  const notes = spans.filter((x) => x.source === "note_span").length;
  const hers = spans[0]?.source === "patient_reply";
  const noteText = notes === 1 ? "one clinical note" : `${notes} clinical notes`;
  return hers ? `Her words, then ${noteText}` : `${noteText[0].toUpperCase()}${noteText.slice(1)} only`;
}

export const restoreSampleData = () => post<{ reloaded: number }>("/api/demo/reset");

export const ROSA_REPLY = "Dejé de trabajar en marzo, la espalda no me aguanta más de diez minutos de pie.";
const BANNED_PATIENT_WORDS = /eligib|elegib/i;

// ---------------------------------------------------------------------------------------------

export const SCENARIOS: Scenario[] = [
  {
    id: "figures",
    group: "figures",
    title: "Headline figures match the accuracy report",
    verifies: "The totals on the work queue (members, cleared by the state's check, cleared by Lapse, one fact away, "
      + "fragile exemptions, claims rejected by the verifier) are identical to the totals in the accuracy report.",
    matters: "Every number shown to a clinic or a health plan must be reproducible from the data, never typed in by hand.",
    tone: "sun",
    mutates: true,
    async run() {
      const c = new Checks();
      await restoreSampleData();
      const s = await api("/api/summary");
      const e = await api("/api/eval");
      c.eq("Members in the sample", s.cohort, 1000);
      c.eq("Cleared by the state's check", s.a_exempt, e.pitch.state_clears);
      c.eq("Cleared by Lapse before contacting anyone", s.final_exempt, e.pitch.lapse_clears_before_outreach);
      c.that("Lapse clears more members than the state's check", s.final_exempt > s.a_exempt,
        "More than the state", `${plain(s.a_exempt)} by the state, ${plain(s.final_exempt)} by Lapse`);
      c.eq("One fact away", s.one_away, e.pitch.one_away);
      c.eq("Fragile exemptions", s.fragile, e.pitch.fragile);
      c.eq("Claims rejected by the verifier", s.verifier_dropped, e.verifier.dropped);
      c.that("The state rule set in use is identified by version", /^ca-/.test(s.rule_pack),
        "A versioned California rule set", s.rule_pack);
      return c.list;
    },
  },
  {
    id: "queue",
    group: "queue",
    title: "The work queue lists the right members, in the right order",
    verifies: "Members renewing in the next 30 days are listed soonest first. Every member who needs action has exactly "
      + "one named next step, and members who are already cleared are given no work.",
    matters: "An enrollment worker has limited time. The queue has to show who to help first and what to do.",
    tone: "sky",
    mutates: false,
    async run() {
      const c = new Checks();
      const q: any[] = await api("/api/queue?window_days=30");
      c.that("Members renewing in the next 30 days are listed", q.length > 0, "At least one", `${q.length} members`);
      c.that("Listed by renewal date, soonest first",
        q.every((r, i) => i === 0 || q[i - 1].renewal_date <= r.renewal_date), "Soonest first");
      c.that("Every renewal falls inside the 30-day window",
        q.every((r) => r.days_to_renewal >= 0 && r.days_to_renewal <= 30), "0 to 30 days away");
      const actionable = q.filter((r) => r.bucket === "ONE_AWAY" || r.bucket === "PROVABLE");
      const missing = actionable.filter((r) => !r.top_missing_fact);
      c.that("Every member who needs action has a next step", missing.length === 0,
        "A next step for each", missing.length ? `${missing.length} without one` : `All ${actionable.length} have one`);
      const noisy = q.filter((r) => r.bucket === "SAFE" && r.top_missing_fact);
      c.that("Members already cleared are given no work", noisy.length === 0, "No work",
        noisy.length ? noisy.map((r) => r.name).join(", ") : "No work");
      return c.list;
    },
  },
  {
    id: "rosa-record",
    group: "journeys",
    title: "What the state sees versus what the chart shows",
    verifies: "For sample member Rosa Delgado, diabetic neuropathy appears only as a secondary billing code, which the "
      + "state's check does not read. Her clinical notes document the condition, every piece of evidence points to an "
      + "exact sentence, and the one missing fact is how long she can stand.",
    matters: "This is the gap Lapse exists to close: people who qualify, but whom a billing-code check cannot see.",
    tone: "clay",
    mutates: false,
    async run() {
      const c = new Checks();
      const r = await api("/api/cases/g-rosa");
      const primary = r.billed_dx_12mo.filter((d: any) => d.sequence === 1).map((d: any) => d.display);
      c.that("Neuropathy is not a primary diagnosis on any claim", !primary.some((d: string) => /neuropath/i.test(d)),
        "Not primary", `Primary diagnoses: ${primary.join(", ")}`);
      c.that("Neuropathy is billed as a secondary diagnosis",
        r.billed_dx_12mo.some((d: any) => d.sequence > 1 && /neuropath/i.test(d.display)), "Billed as secondary");
      c.eq("Result of the state's check", r.determination_a.status, "not_determined");
      const notes = Object.fromEntries(r.notes.map((n: any) => [n.id, n.text]));
      const exact = r.claims.every((cl: any) => notes[cl.note_id]?.slice(cl.start, cl.end) === cl.quote);
      c.that("Every piece of evidence points to its exact sentence", exact, "All exact",
        exact ? `All ${r.claims.length} exact` : "Mismatch found");
      c.eq("The one missing fact", r.missing[0]?.key, "standing_tolerance_minutes");
      c.eq("Who holds it", r.missing[0]?.holder, "patient");
      c.that("The question is ready in her language", !!r.missing[0]?.question?.es, "Spanish",
        r.missing[0]?.question?.es);
      return c.list;
    },
  },
  {
    id: "rosa-loop",
    group: "journeys",
    title: "From one question to a signed attestation",
    verifies: "Rosa is emailed one question in Spanish. Her reply, in her own words, is recorded as evidence and moves "
      + "her case to the clinician. The clinician sees her words and the supporting notes, signs, and a medical "
      + "exemption attestation is produced.",
    matters: "The member answers one question in plain language, with no login or form, and her own words become part of the record.",
    tone: "sun",
    mutates: true,
    async run() {
      const c = new Checks();
      await restoreSampleData();
      const ask = await post("/api/cases/g-rosa/ask");
      c.that("The email asks one question, in Spanish", ask.preview.body.includes("¿"), "Spanish", ask.preview.subject);
      c.that("The email makes no statement about eligibility",
        !BANNED_PATIENT_WORDS.test(ask.preview.subject + ask.preview.body), "No statement");
      const reply = await post("/api/cases/g-rosa/reply", { text: ROSA_REPLY });
      c.eq("Her reply moves the case to", reply.bucket, "PROVABLE");
      c.eq("The case now waits on", reply.status, "waiting_clinician");
      const r = await api("/api/cases/g-rosa");
      const words = r.facts.filter((f: any) => f.source === "patient_reply");
      c.that("Her own words are stored as evidence", words.length > 0 && words.every((f: any) => ROSA_REPLY.includes(f.quote)),
        "Quoted from her reply", words.map((f: any) => `"${f.quote}"`).join(", "));
      c.that("The change is recorded in the audit log", r.events.some((e: any) => e.kind === "case_flipped"), "Recorded");
      const card = await api(`/api${r.clinician_url}`);
      c.that("The clinician is asked one question", /neuropathy/i.test(card.question), "One question", card.question);
      c.that("The clinician sees her words first, then the notes",
        card.spans.length <= 3 && card.spans[0]?.source === "patient_reply", "Her words, then up to two notes",
        describeSpans(card.spans));
      const signed = await post(`/api${r.clinician_url}`, { decision: "sign" });
      c.eq("After signing, the case is", signed.status, "attestation_ready");
      const pdf = await fetch("/api/cases/g-rosa/attestation.pdf", { cache: "no-store" });
      const head = new TextDecoder().decode((await pdf.arrayBuffer()).slice(0, 4));
      c.that("The attestation document is produced", pdf.ok && head === "%PDF", "A PDF document",
        pdf.ok && head === "%PDF" ? "A PDF document" : `Error ${pdf.status}`);
      return c.list;
    },
  },
  {
    id: "deshawn",
    group: "journeys",
    title: "Resolving a fact without contacting anyone",
    verifies: "For sample member Deshawn Price, the missing fact is school enrollment. One lookup in the student "
      + "enrollment database confirms it and clears him. No message is ever sent to him.",
    matters: "Lapse never asks a person for something a database already knows.",
    tone: "sky",
    mutates: true,
    async run() {
      const c = new Checks();
      await restoreSampleData();
      const before = await api("/api/cases/g-deshawn");
      c.eq("The missing fact", before.missing[0]?.key, "enrolled_half_time_school");
      c.eq("Who holds it", before.missing[0]?.holder, "database");
      const out = await post("/api/cases/g-deshawn/check-database");
      c.eq("The lookup confirms enrollment", out.value, true);
      c.eq("His case moves to", out.bucket, "SAFE");
      const after = await api("/api/cases/g-deshawn");
      const last = after.events[after.events.length - 1];
      c.that("The audit log records that nobody was contacted",
        /without contacting anyone/.test(last?.detail?.text ?? ""), "Recorded", last?.detail?.text);
      c.that("No message was sent to him", !after.events.some((e: any) => e.kind === "patient_asked"), "None sent");
      return c.list;
    },
  },
  {
    id: "vague",
    group: "journeys",
    title: "Unclear replies go to a person",
    verifies: "When a member's reply cannot be read with confidence (\"No sé, depende del día\", meaning \"I don't know, "
      + "it depends on the day\"), nothing is recorded, the case does not move, and a person is asked to read it.",
    matters: "The system never guesses at what a member meant.",
    tone: "stone",
    mutates: true,
    async run() {
      const c = new Checks();
      await restoreSampleData();
      await post("/api/cases/g-rosa/ask");
      const r = await post("/api/cases/g-rosa/reply", { text: "No sé, depende del día." });
      c.eq("The reply is read as an answer", r.parsed, false);
      const rosa = await api("/api/cases/g-rosa");
      c.eq("The case stays at", rosa.bucket, "ONE_AWAY");
      c.that("A person is asked to read the reply",
        rosa.events[rosa.events.length - 1]?.kind === "reply_needs_human_read", "Flagged for review");
      return c.list;
    },
  },
  {
    id: "karen",
    group: "journeys",
    title: "Flagging exemptions the chart does not support",
    verifies: "Sample member Karen Hollis is exempted by the state on a depression billing code, but no verified "
      + "sentence in her notes shows the condition limits her. Lapse flags the exemption as fragile.",
    matters: "Health plans want to know which exemptions would not survive an audit, before an auditor finds them.",
    tone: "clay",
    mutates: false,
    async run() {
      const c = new Checks();
      const fragile: any[] = await api("/api/fragile");
      c.that("She appears on the fragile list", fragile.some((f) => f.patient_id === "g-karen"), "On the list",
        `On the list, with ${fragile.length - 1} others`);
      const k = await api("/api/cases/g-karen");
      c.eq("The state exempts her through", k.determination_a.rule_ids, ["medically_frail"]);
      c.that("No verified sentence shows a limitation",
        !k.claims.some((cl: any) => cl.verified && cl.significantly_impairs === "true"), "None found");
      return c.list;
    },
  },
  {
    id: "bea",
    group: "safeguards",
    title: "Information about relatives is never used as evidence",
    verifies: "Sample member Bea Knox's notes say \"Patient's mother has severe arthritis.\" That sentence is about her "
      + "mother, so it must never count as evidence about Bea.",
    matters: "Evidence has to be about the member, current, and stated. A family history does not qualify anyone.",
    tone: "sage",
    mutates: true,
    async run() {
      const c = new Checks();
      await restoreSampleData();
      const b = await api("/api/cases/g-bea");
      c.that("The sentence is present in her notes",
        b.notes.some((n: any) => /mother has severe arthritis/i.test(n.text)), "Present");
      c.that("It is not used as evidence", !b.claims.some((cl: any) => /mother/i.test(cl.quote)), "Not used");
      const dropped = b.dropped_claims.find((cl: any) => /mother/i.test(cl.quote));
      c.that("Where it was stopped", true, "Before it could count",
        dropped ? `Rejected by the verifier: ${dropped.verifier_reason}` : "Never proposed by the evidence finder");
      c.eq("Her case", b.bucket, "NO_PATH");
      return c.list;
    },
  },
  {
    id: "eval",
    group: "safeguards",
    title: "Accuracy is reported honestly",
    verifies: "The accuracy report says it was measured on synthetic data, shows Lapse finding more qualifying members "
      + "than the state's check without lowering precision, and marks any simulated figure as simulated.",
    matters: "A claim of accuracy is only useful if it states what it was measured against.",
    tone: "sky",
    mutates: false,
    async run() {
      const c = new Checks();
      const e = await api("/api/eval");
      const pl = e.patient_level;
      c.that("States that it was measured on synthetic data", /synthetic/i.test(e.label), "Stated", e.label);
      c.that("Lapse finds more qualifying members than the state (recall)", pl.final.r > pl.a.r, "Higher",
        `${pl.a.r} for the state, ${pl.final.r} for Lapse`);
      c.that("Without lowering precision", pl.final.p >= pl.a.p, "Equal or higher",
        `${pl.a.p} for the state, ${pl.final.p} for Lapse`);
      c.eq("The after-outreach figure is marked simulated", pl.after_outreach.simulated, true);
      c.that("Evidence precision is at least 0.90", e.claim_level.p >= 0.9, "0.90 or more", String(e.claim_level.p));
      c.that("Claims rejected by the verifier are counted", e.verifier.dropped > 0, "Counted",
        `${e.verifier.dropped} rejected`);
      return c.list;
    },
  },
  {
    id: "copy",
    group: "safeguards",
    title: "Member communications follow the language rules",
    verifies: "Messages to members never say whether they are eligible, the product never calls its output a "
      + "\"renewal application\", and clinicians are told that the state makes the eligibility decision.",
    matters: "Lapse assembles evidence. Only the state decides eligibility, and the wording must say so.",
    tone: "stone",
    mutates: true,
    async run() {
      const c = new Checks();
      await restoreSampleData();
      const ask = await post("/api/cases/g-rosa/ask");
      const rosa = await api("/api/cases/g-rosa");
      const card = await api(`/api${rosa.clinician_url}`);
      const summary = await api("/api/summary");
      c.that("The member email makes no statement about eligibility",
        !BANNED_PATIENT_WORDS.test(ask.preview.subject + ask.preview.body), "No statement");
      c.that("The term \"renewal application\" is never used",
        !/renewal application/i.test(JSON.stringify([ask, rosa, card, summary])), "Never used");
      c.that("The clinician is told the state decides", /state makes the eligibility decision/i.test(card.attesting_to ?? ""),
        "Told", card.attesting_to);
      await restoreSampleData();
      return c.list;
    },
  },
];

// ---------------------------------------------------------------------------------------------
// Engine test suite

export interface EngineTest {
  file: string;
  name: string;
  outcome: "passed" | "failed" | "error" | "skipped";
  seconds: number;
  message: string;
}

export interface EngineRun {
  ok: boolean;
  seconds: number;
  passed: number;
  failed: number;
  error: number;
  skipped: number;
  tests: EngineTest[];
  summary: string;
}

/** What each test file covers, for readers who do not know the codebase. */
export const SUITE_AREAS: Record<string, string> = {
  "test_golden.py": "Sample member records",
  "test_cohort.py": "Synthetic member data and labels",
  "test_channel_a.py": "The state's eligibility check (simulated)",
  "test_channel_b.py": "The evidence finder and verifier",
  "test_solver.py": "Finding the one missing fact",
  "test_eval.py": "Accuracy measurement",
  "test_llm.py": "Language model client",
  "test_bedrock_llm.py": "Language model client (AWS Bedrock)",
  "test_loop.py": "Member and clinician workflow",
  "test_track_b_integration.py": "End-to-end workflow",
  "test_b5_appeal.py": "Appeal evidence packet",
  "test_b5_voice.py": "Voice calls for members who do not reply",
};

export const runEngineSuite = () => post<EngineRun>("/api/dev/tests");
