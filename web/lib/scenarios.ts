// Demo QA scenarios for the /tests page. Each one drives the live API the way the demo does and returns named
// checks. Scenarios that change data reset the golden cases first, so any of them can run on its own.

export type Tone = "sky" | "sun" | "clay" | "stone" | "sage";

export interface Check {
  name: string;
  ok: boolean;
  detail?: string;
}

export interface Scenario {
  id: string;
  title: string;
  blurb: string;
  tone: Tone;
  mutates: boolean;
  run: () => Promise<Check[]>;
}

class HttpError extends Error {}

async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  if (!res.ok) throw new HttpError(`${init?.method ?? "GET"} ${path} -> ${res.status} ${await res.text()}`);
  return res.json();
}

const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });

class Checks {
  list: Check[] = [];
  ok(name: string, ok: boolean, detail?: string) {
    this.list.push({ name, ok, detail });
    return ok;
  }
  eq(name: string, actual: unknown, expected: unknown) {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    return this.ok(name, a === e, a === e ? a : `expected ${e}, got ${a}`);
  }
}

const reset = () => post<{ reloaded: number }>("/api/demo/reset");

export const ROSA_REPLY = "Dejé de trabajar en marzo, la espalda no me aguanta más de diez minutos de pie.";
const BANNED_PATIENT_WORDS = /eligib|elegib/i;

export const SCENARIOS: Scenario[] = [
  {
    id: "reset",
    title: "Reset the demo",
    blurb: "The seven golden patients go back to their pre-demo state in one call.",
    tone: "stone",
    mutates: true,
    async run() {
      const c = new Checks();
      c.eq("Reset reloads 7 golden cases", (await reset()).reloaded, 7);
      c.eq("Rosa is back to one fact away", (await api("/api/cases/g-rosa")).bucket, "ONE_AWAY");
      c.eq("Deshawn is back to one fact away", (await api("/api/cases/g-deshawn")).bucket, "ONE_AWAY");
      return c.list;
    },
  },
  {
    id: "numbers",
    title: "The counters match the eval",
    blurb: "What the reveal animates on screen equals what the accuracy run measured.",
    tone: "sun",
    mutates: true,
    async run() {
      const c = new Checks();
      await reset();
      const s = await api("/api/summary");
      const e = await api("/api/eval");
      c.eq("Cohort is 1,000", s.cohort, 1000);
      c.eq("State clears (compliant or exempt)", s.a_exempt, e.pitch.state_clears);
      c.eq("Lapse clears before outreach", s.final_exempt, e.pitch.lapse_clears_before_outreach);
      c.ok("Lapse clears more than the state", s.final_exempt > s.a_exempt, `${s.a_exempt} -> ${s.final_exempt}`);
      c.eq("One fact away", s.one_away, e.pitch.one_away);
      c.eq("Fragile", s.fragile, e.pitch.fragile);
      c.ok("Rule pack shows its version", /^ca-/.test(s.rule_pack), s.rule_pack);
      return c.list;
    },
  },
  {
    id: "queue",
    title: "The morning queue",
    blurb: "Renewals in the next 30 days, soonest first, each with the one thing to do.",
    tone: "sky",
    mutates: false,
    async run() {
      const c = new Checks();
      const q: any[] = await api("/api/queue?window_days=30");
      c.ok("Queue is not empty", q.length > 0, `${q.length} renewing in 30 days`);
      const sorted = q.every((r, i) => i === 0 || q[i - 1].renewal_date <= r.renewal_date);
      c.ok("Sorted by renewal date", sorted);
      c.ok("All inside the 30-day window", q.every((r) => r.days_to_renewal >= 0 && r.days_to_renewal <= 30));
      const actionable = q.filter((r) => r.bucket === "ONE_AWAY" || r.bucket === "PROVABLE");
      c.ok("Every actionable row names its next step", actionable.every((r) => r.top_missing_fact),
        `${actionable.length} actionable rows`);
      const noisy = q.filter((r) => r.bucket === "SAFE" && r.top_missing_fact);
      c.ok("SAFE rows ask for nothing", noisy.length === 0,
        noisy.length ? noisy.map((r) => `${r.name}: ${r.top_missing_fact.key}`).join("; ") : undefined);
      return c.list;
    },
  },
  {
    id: "rosa-record",
    title: "Rosa: what the state sees vs. the chart",
    blurb: "Neuropathy is billed only as a secondary code; the notes name it, but not how it limits her.",
    tone: "clay",
    mutates: false,
    async run() {
      const c = new Checks();
      const r = await api("/api/cases/g-rosa");
      const primary = r.billed_dx_12mo.filter((d: any) => d.sequence === 1).map((d: any) => d.display);
      c.ok("No neuropathy among primary codes", !primary.some((d: string) => /neuropath/i.test(d)), primary.join(", "));
      c.ok("Neuropathy billed as a secondary code",
        r.billed_dx_12mo.some((d: any) => d.sequence > 1 && /neuropath/i.test(d.display)));
      c.eq("State cannot determine", r.determination_a.status, "not_determined");
      const notes = Object.fromEntries(r.notes.map((n: any) => [n.id, n.text]));
      const exact = r.claims.every((cl: any) => notes[cl.note_id]?.slice(cl.start, cl.end) === cl.quote);
      c.ok("Every claim points at its exact sentence", exact, `${r.claims.length} claims`);
      c.eq("Top missing fact", [r.missing[0]?.key, r.missing[0]?.holder], ["standing_tolerance_minutes", "patient"]);
      c.ok("Question is ready in Spanish", !!r.missing[0]?.question?.es, r.missing[0]?.question?.es);
      return c.list;
    },
  },
  {
    id: "rosa-loop",
    title: "Rosa: ask, reply, sign, PDF",
    blurb: "One question in Spanish; her own words flip the case; the clinician signs; the attestation renders.",
    tone: "sun",
    mutates: true,
    async run() {
      const c = new Checks();
      await reset();
      const ask = await post("/api/cases/g-rosa/ask");
      c.ok("Email asks one question in Spanish", ask.preview.body.includes("¿"), ask.preview.subject);
      c.ok("Email never mentions eligibility", !BANNED_PATIENT_WORDS.test(ask.preview.subject + ask.preview.body));
      const reply = await post("/api/cases/g-rosa/reply", { text: ROSA_REPLY });
      c.eq("Her reply flips the case", [reply.bucket, reply.status], ["PROVABLE", "waiting_clinician"]);
      const r = await api("/api/cases/g-rosa");
      const words = r.facts.filter((f: any) => f.source === "patient_reply");
      c.ok("Her words are stored as evidence", words.length > 0 && words.every((f: any) => ROSA_REPLY.includes(f.quote)),
        words.map((f: any) => `${f.key}: "${f.quote}"`).join("; "));
      c.ok("Flip is in the audit log", r.events.some((e: any) => e.kind === "case_flipped"));
      const card = await api(`/api${r.clinician_url}`);
      c.ok("Clinician gets one question", /neuropathy/i.test(card.question), card.question);
      c.ok("Up to three sentences, hers first", card.spans.length <= 3 && card.spans[0]?.source === "patient_reply",
        card.spans.map((s: any) => s.source).join(", "));
      const signed = await post(`/api${r.clinician_url}`, { decision: "sign" });
      c.eq("Signing makes the attestation ready", signed.status, "attestation_ready");
      const pdf = await fetch("/api/cases/g-rosa/attestation.pdf", { cache: "no-store" });
      const head = new TextDecoder().decode((await pdf.arrayBuffer()).slice(0, 4));
      c.ok("Attestation PDF renders", pdf.ok && head === "%PDF", `${pdf.status} ${head}`);
      return c.list;
    },
  },
  {
    id: "deshawn",
    title: "Deshawn: one database click",
    blurb: "Student enrollment confirms the exemption. Nobody is contacted.",
    tone: "sky",
    mutates: true,
    async run() {
      const c = new Checks();
      await reset();
      const before = await api("/api/cases/g-deshawn");
      c.eq("Next step is a database", [before.missing[0]?.key, before.missing[0]?.holder],
        ["enrolled_half_time_school", "database"]);
      const out = await post("/api/cases/g-deshawn/check-database");
      c.eq("Lookup confirms enrollment", out.value, true);
      c.eq("Case moves to SAFE", out.bucket, "SAFE");
      const after = await api("/api/cases/g-deshawn");
      const last = after.events[after.events.length - 1];
      c.ok("Logged as resolved without contact", /without contacting anyone/.test(last?.detail?.text ?? ""), last?.detail?.text);
      c.ok("No message was ever sent", !after.events.some((e: any) => e.kind === "patient_asked"));
      return c.list;
    },
  },
  {
    id: "vague",
    title: "A reply nobody can parse",
    blurb: "When the answer is unclear, a person reads it. The system never guesses.",
    tone: "stone",
    mutates: true,
    async run() {
      const c = new Checks();
      await reset();
      await post("/api/cases/g-rosa/ask");
      const r = await post("/api/cases/g-rosa/reply", { text: "No sé, depende del día." });
      c.eq("Reply is not parsed", r.parsed, false);
      const rosa = await api("/api/cases/g-rosa");
      c.eq("Case does not move", rosa.bucket, "ONE_AWAY");
      c.eq("Flagged for a human read", rosa.events[rosa.events.length - 1]?.kind, "reply_needs_human_read");
      return c.list;
    },
  },
  {
    id: "karen",
    title: "Karen: exempt, but fragile",
    blurb: "The state exempts her on a depression code; not one verified sentence supports an impairment.",
    tone: "clay",
    mutates: false,
    async run() {
      const c = new Checks();
      const fragile: any[] = await api("/api/fragile");
      c.ok("Karen is on the fragile list", fragile.some((f) => f.patient_id === "g-karen"), `${fragile.length} fragile`);
      const k = await api("/api/cases/g-karen");
      c.eq("State exempts on medical frailty", k.determination_a.rule_ids, ["medically_frail"]);
      c.ok("No verified sentence shows an impairment",
        !k.claims.some((cl: any) => cl.verified && cl.significantly_impairs === "true"));
      return c.list;
    },
  },
  {
    id: "bea",
    title: "Bea: the bait is dropped",
    blurb: "\"Patient's mother has severe arthritis\" is about her mother. It never counts.",
    tone: "sage",
    mutates: true,
    async run() {
      const c = new Checks();
      await reset();
      const b = await api("/api/cases/g-bea");
      c.eq("No path within one fact", b.bucket, "NO_PATH");
      const bait = b.dropped_claims.find((cl: any) => /mother/i.test(cl.quote));
      c.ok("Bait claim was dropped", !!bait, bait?.quote);
      c.ok("Verifier says why", !!bait?.verifier_reason, bait?.verifier_reason);
      c.ok("Nothing verified mentions her mother", !b.claims.some((cl: any) => /mother/i.test(cl.quote)));
      return c.list;
    },
  },
  {
    id: "eval",
    title: "Accuracy, measured honestly",
    blurb: "Recall against the state's method, labeled as measured on synthetic data.",
    tone: "sky",
    mutates: false,
    async run() {
      const c = new Checks();
      const e = await api("/api/eval");
      const pl = e.patient_level;
      c.ok("Labeled as synthetic", /synthetic/i.test(e.label), e.label);
      c.ok("Lapse recall beats the state's", pl.final.r > pl.a.r, `${pl.a.r} -> ${pl.final.r}`);
      c.ok("Precision does not drop", pl.final.p >= pl.a.p, `${pl.a.p} -> ${pl.final.p}`);
      c.eq("Outreach figure marked simulated", pl.after_outreach.simulated, true);
      c.ok("Claim precision at least 0.9", e.claim_level.p >= 0.9, String(e.claim_level.p));
      c.ok("Verifier drops are counted", e.verifier.dropped > 0, `${e.verifier.dropped} dropped`);
      return c.list;
    },
  },
  {
    id: "copy",
    title: "Words that must never appear",
    blurb: "No eligibility talk to patients; never \"renewal application\" anywhere.",
    tone: "stone",
    mutates: true,
    async run() {
      const c = new Checks();
      await reset();
      const ask = await post("/api/cases/g-rosa/ask");
      const rosa = await api("/api/cases/g-rosa");
      const card = await api(`/api${rosa.clinician_url}`);
      const summary = await api("/api/summary");
      c.ok("Patient email: no eligibility statement", !BANNED_PATIENT_WORDS.test(ask.preview.subject + ask.preview.body));
      const everything = JSON.stringify([ask, rosa, card, summary]);
      c.ok("Nowhere says \"renewal application\"", !/renewal application/i.test(everything));
      c.ok("Clinician is told the state decides", /state makes the eligibility decision/i.test(card.attesting_to ?? ""),
        card.attesting_to);
      await reset();
      return c.list;
    },
  },
];

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

export const runEngineSuite = () => post<EngineRun>("/api/dev/tests");
export const resetDemo = reset;
