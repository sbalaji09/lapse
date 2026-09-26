"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { apiFetch } from "@/lib/api";
import type { Bucket, Case, Claim, MissingFact, Note } from "@/lib/types";
import {
  BUCKET_HINT, BUCKET_LABEL, FACT_LABEL, LANGUAGE_LABEL, SOURCE_LABEL, STATUS_LABEL, firstName, formatValue,
  nextStepLabel, ruleList,
} from "@/components/labels";
import { BucketTag, Footer, Nav, SourceMark, u } from "@/components/ui";
import c from "./case.module.css";

type CaseDetail = Case & { notes: Note[]; clinician_url: string };
type Dx = { claim_id: string; date: string; code: string; display: string; sequence: number };
type Event = { at: string; kind: string; detail: any };
type GuardrailResult = {
  allowed: boolean;
  action: string;
  output: string;
  provider: string;
  topics: Array<{ name: string; type: string; action: string; detected?: boolean }>;
};

const UNSAFE_PATIENT_MESSAGE =
  "Rosa, you are exempt from the work requirement and will keep your Medi-Cal coverage.";

// Rosa's pre-written reply for the demo, with the English gloss shown under it.
const DEMO_REPLIES: Record<string, { text: string; gloss: string }> = {
  "g-rosa": {
    text: "Dejé de trabajar en marzo, la espalda no me aguanta más de diez minutos de pie.",
    gloss: "\"I stopped working in March; I can't stay on my feet more than ten minutes.\"",
  },
};

const HOLDER_SOURCE = { patient: "patient_reply", database: "external_db", clinician: "clinician_attestation" } as const;
const MISSING_STATUS: Record<string, string> = {
  open: "Open", asked: "Asked", answered: "Answered", resolved_true: "Confirmed", resolved_false: "Ruled out",
};

function openStep(missing: MissingFact[]): MissingFact | undefined {
  return missing.find((m) => m.status === "open" || m.status === "asked");
}

function eventText(e: Event, name: string): string {
  const d = e.detail;
  switch (e.kind) {
    case "pipeline_run": return typeof d === "string" ? d : "Overnight run";
    case "patient_asked": return `Emailed ${firstName(name)} the question: "${d?.subject ?? ""}"`;
    case "patient_reply_received": return `${firstName(name)} replied: "${d?.text ?? ""}"`;
    case "case_flipped": return `Moved from ${BUCKET_LABEL[d?.from as Bucket] ?? d?.from} to ${BUCKET_LABEL[d?.to as Bucket] ?? d?.to}`;
    case "reply_needs_human_read": return "Reply needs a human read. Nothing was guessed.";
    case "database_checked": return d?.text ?? "Checked a database";
    case "clinician_signed": return `Attestation signed by ${d?.clinician_name ?? "the clinician"}`;
    case "clinician_declined": return `${d?.clinician_name ?? "The clinician"} declined to sign`;
    default: return typeof d === "string" ? d : e.kind.replaceAll("_", " ");
  }
}

function when(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** A note with every verified claim's exact span highlighted and addressable. */
function NoteText({ note, claims, flash }: { note: Note; claims: Claim[]; flash: string | null }) {
  const spans = claims.filter((cl) => cl.note_id === note.id).sort((a, b) => a.start - b.start);
  const parts: React.ReactNode[] = [];
  let at = 0;
  for (const cl of spans) {
    if (cl.start < at) continue;                    // overlapping claims: keep the first
    parts.push(note.text.slice(at, cl.start));
    parts.push(
      <mark key={cl.id} id={`span-${cl.id}`} className={`${c.span} ${flash === cl.id ? c.spanFlash : ""}`}>
        {note.text.slice(cl.start, cl.end)}
      </mark>,
    );
    at = cl.end;
  }
  parts.push(note.text.slice(at));
  return <p className={c.noteText}>{parts}</p>;
}

export default function CaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [flipped, setFlipped] = useState<{ from: Bucket; to: Bucket } | null>(null);
  const [reply, setReply] = useState("");
  const [waitingOnClinician, setWaitingOnClinician] = useState(false);
  const [guardrail, setGuardrail] = useState<GuardrailResult | null>(null);
  const [guardrailBusy, setGuardrailBusy] = useState(false);
  const [guardrailError, setGuardrailError] = useState<string | null>(null);
  const bucketRef = useRef<Bucket | null>(null);

  const load = useCallback(async () => {
    const res = await apiFetch(`/api/cases/${id}`, { cache: "no-store" });
    if (!res.ok) {
      setError(res.status === 404 ? "No such case." : `Could not load the case (${res.status}).`);
      return null;
    }
    const d: CaseDetail = await res.json();
    if (bucketRef.current && bucketRef.current !== d.bucket) setFlipped({ from: bucketRef.current, to: d.bucket });
    bucketRef.current = d.bucket;
    setData(d);
    return d;
  }, [id]);

  useEffect(() => {
    load();
    setReply(DEMO_REPLIES[id]?.text ?? "");
    setGuardrail(null);
    setGuardrailError(null);
  }, [id, load]);

  // After "Send to Dr. X", watch for the signature landing in the other tab.
  useEffect(() => {
    if (!waitingOnClinician) return;
    const t = setInterval(async () => {
      const d = await load();
      if (d && d.status !== "waiting_clinician") setWaitingOnClinician(false);
    }, 1000);
    const stop = setTimeout(() => setWaitingOnClinician(false), 180_000);
    return () => { clearInterval(t); clearTimeout(stop); };
  }, [waitingOnClinician, load]);

  async function act(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch(path, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) setError((await res.json().catch(() => null))?.detail ?? `Request failed (${res.status})`);
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function challengeGuardrail() {
    setGuardrailBusy(true);
    setGuardrailError(null);
    try {
      const response = await apiFetch("/api/guardrails/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: UNSAFE_PATIENT_MESSAGE }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? `Guardrail check failed (${response.status})`);
      setGuardrail(result);
    } catch (cause) {
      setGuardrailError(cause instanceof Error ? cause.message : "The Guardrail check could not run.");
    } finally {
      setGuardrailBusy(false);
    }
  }

  function showSpan(claimId: string) {
    // Jump, then flash: the flash is the motion; smooth scrolling is throttled in background tabs.
    document.getElementById(`span-${claimId}`)?.scrollIntoView({ block: "center" });
    setFlash(null);
    requestAnimationFrame(() => setFlash(claimId));
  }

  if (error && !data) return (<><Nav /><main className={u.wrap}><p className={c.back}>{error}</p></main></>);
  if (!data) return (<><Nav /><main className={u.wrap}><p className={u.muted}>Loading…</p></main></>);

  const step = openStep(data.missing);
  const events = data.events as Event[];
  const lastEmail = [...events].reverse().find((e) => e.kind === "patient_asked")?.detail;
  const dx = data.billed_dx_12mo as Dx[];
  const byClaim = dx.reduce<Record<string, Dx[]>>((acc, d) => ((acc[d.claim_id] ??= []).push(d), acc), {});
  const claimOrder = Object.keys(byClaim).sort((a, b) => byClaim[b][0].date.localeCompare(byClaim[a][0].date));
  const days = Math.round((new Date(data.renewal_date).getTime() - new Date("2027-02-15").getTime()) / 86_400_000);
  const first = firstName(data.display_name);
  const a = data.determination_a;

  return (
    <>
      <Nav />
      <main className={u.wrap}>
        <Link href="/" className={c.back}>← Queue</Link>
        <div className={c.header}>
          <div>
            <h1 className={u.h1}>{data.display_name}</h1>
            <p className={c.meta}>
              {data.age} · {LANGUAGE_LABEL[data.language] ?? data.language} · {data.clinician_name} ·
              renews {data.renewal_date} (in {days} days)
            </p>
          </div>
          <div className={c.headRight}>
            <BucketTag bucket={data.bucket} big flip={!!flipped} key={data.bucket} />
            <span className={u.muted}>{STATUS_LABEL[data.status]}</span>
            {data.fragile && <span className={u.fragile}>▲ Fragile: the state exempts on a code the chart does not support</span>}
          </div>
        </div>

        {flipped && (
          <p className={c.banner} role="status">
            Moved from {BUCKET_LABEL[flipped.from]} to {BUCKET_LABEL[flipped.to]}.
          </p>
        )}

        {/* The one thing to do */}
        <section className={`${c.action} ${step ? "" : c.actionQuiet}`} aria-label="Next step">
          <div>
            <p className={u.eyebrow}>Next step</p>
            {step ? (
              <>
                <p className={c.why}>{step.why}</p>
                {step.holder !== "database" && step.question?.en && (
                  <p className={c.question}>Question: {step.question[data.language] ?? step.question.en}</p>
                )}
              </>
            ) : data.status === "attestation_ready" ? (
              <p className={c.why}>Signed. The medical exemption attestation is ready to submit.</p>
            ) : (
              <p className={c.why}>{BUCKET_HINT[data.bucket]}</p>
            )}
          </div>
          <div>
            {step?.holder === "patient" && step.status === "open" && (
              <button className={u.btnDark} disabled={busy} onClick={() => act(`/api/cases/${id}/ask`)}>
                {nextStepLabel(step, data.display_name, data.clinician_name)} →
              </button>
            )}
            {step?.holder === "database" && (
              <button className={u.btnDark} disabled={busy} onClick={() => act(`/api/cases/${id}/check-database`)}>
                {nextStepLabel(step, data.display_name, data.clinician_name)} →
              </button>
            )}
            {step?.holder === "clinician" && (
              <a className={u.btnDark} href={data.clinician_url} target="_blank" rel="noreferrer"
                onClick={() => setWaitingOnClinician(true)}>
                {nextStepLabel(step, data.display_name, data.clinician_name)} ↗
              </a>
            )}
            {!step && data.status === "attestation_ready" && (
              <a className={u.btnDark} href={`/api/cases/${id}/attestation.pdf`} target="_blank" rel="noreferrer">
                Open attestation PDF ↗
              </a>
            )}
          </div>

          {id === "g-rosa" && step?.holder === "patient" && step.status === "open" && (
            <aside className={c.guardrailBoundary} aria-labelledby="guardrail-title">
              <div className={c.guardrailDraft}>
                <div className={c.guardrailMark}>
                  <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.7"
                    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M12 3 19 6v5c0 4.8-2.7 8.1-7 10-4.3-1.9-7-5.2-7-10V6l7-3Z" />
                    <path d="M8.5 12h7" />
                  </svg>
                  <span>
                    <strong>Outbound safety challenge</strong>
                    Amazon Bedrock Guardrails
                  </span>
                </div>
                <h2 id="guardrail-title" className={c.guardrailTitle}>Try the message Lapse must refuse.</h2>
                <p className={c.guardrailCopy}>This draft makes an eligibility decision that only the state can make.</p>
                <blockquote className={c.guardrailQuote}>&ldquo;{UNSAFE_PATIENT_MESSAGE}&rdquo;</blockquote>
              </div>

              <div className={c.guardrailDecision}>
                {guardrail ? (
                  <div className={`${c.guardrailResult} ${guardrail.allowed ? c.guardrailFailed : ""}`} role="status">
                    <div className={c.guardrailResultHead}>
                      <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="1.8"
                        strokeLinecap="round" aria-hidden="true">
                        <circle cx="12" cy="12" r="9" />
                        <path d="M8 12h8" />
                      </svg>
                      <span>
                        <strong>{guardrail.allowed ? "Not blocked — do not send" : "Blocked before sending"}</strong>
                        {guardrail.provider}
                      </span>
                    </div>
                    <p className={c.guardrailOutcome}>
                      {guardrail.allowed
                        ? "The safety boundary did not intervene."
                        : "Rosa did not receive this draft. Her case and activity log are unchanged."}
                    </p>
                    {!guardrail.allowed && guardrail.output && (
                      <>
                        <p className={c.guardrailLabel}>Safe guidance returned</p>
                        <blockquote className={c.guardrailSafe}>&ldquo;{guardrail.output}&rdquo;</blockquote>
                      </>
                    )}
                    <dl className={c.guardrailFacts}>
                      <div>
                        <dt>Denied topic</dt>
                        <dd>{guardrail.topics.find((topic) => topic.detected)?.name ?? "None detected"}</dd>
                      </div>
                      <div>
                        <dt>AWS action</dt>
                        <dd>{guardrail.action.replaceAll("_", " ")}</dd>
                      </div>
                    </dl>
                  </div>
                ) : (
                  <>
                    <p className={c.guardrailLabel}>At the send boundary</p>
                    <p className={c.guardrailStandby}>The candidate is still inside Lapse. Challenge the live policy before using the reviewed question above.</p>
                  </>
                )}

                {guardrailError && (
                  <p className={c.guardrailError} role="alert">
                    {guardrailError} Run the Guardrail provision command, then try again.
                  </p>
                )}

                <button className={u.btnDark} onClick={challengeGuardrail} disabled={guardrailBusy}>
                  {guardrailBusy ? "Checking with Bedrock…" : guardrail ? "Challenge it again" : "Attempt unsafe send →"}
                </button>
              </div>
            </aside>
          )}

          {step?.holder === "patient" && step.status === "asked" && (
            <div className={c.email}>
              <div className={c.mail}>
                <div className={c.mailHead}>Sent to {first} · {data.email}</div>
                <div className={c.mailSubject}>{lastEmail?.subject}</div>
                <pre className={c.mailBody}>{lastEmail?.body}</pre>
              </div>
              <div className={`${c.mail} ${c.reply}`}>
                <label className={c.mailHead} htmlFor="reply">{first}&apos;s reply (simulated inbound email)</label>
                <textarea id="reply" value={reply} onChange={(e) => setReply(e.target.value)} />
                {DEMO_REPLIES[id] && reply === DEMO_REPLIES[id].text && <p className={c.gloss}>{DEMO_REPLIES[id].gloss}</p>}
                <button className={u.btnDark} disabled={busy || !reply.trim()}
                  onClick={() => act(`/api/cases/${id}/reply`, { text: reply })}>
                  {first} replied →
                </button>
              </div>
            </div>
          )}
          {waitingOnClinician && <p className={u.faint}>Waiting for {data.clinician_name} to sign in the other tab…</p>}
          {error && <p className={c.error}>{error}</p>}
        </section>

        <div className={c.cols}>
          {/* 1. What the state sees */}
          <section className={c.col} aria-labelledby="state">
            <h2 id="state" className={c.colHead}>What the state sees</h2>
            <p className={c.colSub}>Billed diagnoses, last 12 months. The state reads only the primary one.</p>
            {claimOrder.map((cid) => (
              <div key={cid} className={c.dx}>
                <div className={c.dxDate}>{byClaim[cid][0].date}</div>
                {byClaim[cid].sort((x, y) => x.sequence - y.sequence).map((d) => (
                  <div key={d.code + d.sequence} className={d.sequence === 1 ? c.dxPrimary : c.dxSecondary}>
                    {d.display}
                    {d.sequence !== 1 && <em>not read by the state</em>}
                  </div>
                ))}
              </div>
            ))}
            <div className={c.result}>
              <strong>State result:</strong>{" "}
              {a.status === "not_determined" ? "cannot determine. A notice goes out."
                : `${a.status === "compliant" ? "meets the requirement" : "exempt"} through ${ruleList(a.rule_ids)}.`}
            </div>
          </section>

          {/* 2. What the chart says */}
          <section className={c.col} aria-labelledby="chart">
            <h2 id="chart" className={c.colHead}>What the chart says</h2>
            <p className={c.colSub}>Every claim points at its exact sentence. Click one to see it.</p>
            {data.claims.length === 0 && <p className={u.faint}>No verified evidence in the notes.</p>}
            <div className={c.claims}>
              {data.claims.map((cl) => (
                <button key={cl.id} className={c.claimBtn} onClick={() => showSpan(cl.id)}>
                  <SourceMark source="note_span" />
                  <span>
                    <span className={c.claimCond}>{cl.condition}</span>
                    {cl.significantly_impairs === "true" && <span className={c.limits}>states a limitation</span>}
                    <span className={c.claimQuote}>&ldquo;{cl.quote}&rdquo;</span>
                  </span>
                </button>
              ))}
            </div>
            {data.dropped_claims.length > 0 && (
              <details className={c.dropped}>
                <summary>Verifier dropped {data.dropped_claims.length} claim{data.dropped_claims.length > 1 ? "s" : ""}</summary>
                {data.dropped_claims.map((cl) => (
                  <div key={cl.id} className={c.droppedItem}>
                    <s>&ldquo;{cl.quote}&rdquo;</s>
                    <div className={u.faint}>{cl.verifier_reason}</div>
                  </div>
                ))}
              </details>
            )}
            {data.notes.map((n) => (
              <article key={n.id} className={c.note}>
                <div className={c.noteHead}>{n.date} · {n.author}</div>
                <NoteText note={n} claims={data.claims} flash={flash} />
              </article>
            ))}
          </section>

          {/* 3. What's missing and who knows it */}
          <section className={c.col} aria-labelledby="missing">
            <h2 id="missing" className={c.colHead}>What&apos;s missing</h2>
            <p className={c.colSub}>And who knows it. Databases first, then the clinician, then the patient.</p>
            <div className={c.missing}>
              {data.missing.length === 0 && <p className={u.faint}>Nothing is missing.</p>}
              {data.missing.map((m) => (
                <div key={m.id} className={`${c.missingItem} ${m.status === "open" || m.status === "asked" ? "" : c.missingDone}`}>
                  <div className={c.missingTop}>
                    <span className={u.markRow}>
                      <SourceMark source={HOLDER_SOURCE[m.holder]} />
                      {FACT_LABEL[m.key] ?? m.key}
                    </span>
                    <span className={c.pill}>{MISSING_STATUS[m.status] ?? m.status}</span>
                  </div>
                  <div className={u.faint}>Held by the {m.holder}{m.database ? ` (${m.database.replaceAll("_", " ")})` : ""}</div>
                </div>
              ))}
            </div>

            <h3 className={u.h3}>Evidence log</h3>
            <ul className={c.log}>
              {data.facts.map((f) => (
                <li key={f.id} className={c.logItem}>
                  <SourceMark source={f.source} />
                  <span>
                    <strong>{FACT_LABEL[f.key] ?? f.key}:</strong> {formatValue(f.value)}
                    {f.quote && <span className={c.logQuote}>&ldquo;{f.quote}&rdquo;</span>}
                    <span className={c.logWhen}>{SOURCE_LABEL[f.source]} · {when(f.recorded_at)}</span>
                  </span>
                </li>
              ))}
            </ul>

            <h3 className={u.h3}>Timeline</h3>
            <ol className={c.timeline}>
              {events.map((e, i) => (
                <li key={i} className={c.event}>
                  {eventText(e, data.display_name)}
                  <span className={c.eventWhen}>{when(e.at)}</span>
                </li>
              ))}
            </ol>
          </section>
        </div>
      </main>
      <Footer />
    </>
  );
}
