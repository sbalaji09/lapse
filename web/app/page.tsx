"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, apiUrl } from "@/lib/api";
import type { Bucket, CaseStatus, Holder } from "@/lib/types";
import { BUCKET_LABEL, LANGUAGE_LABEL, nextStepShort } from "@/components/labels";
import { AnimatedNumber, BucketTag, Footer, Nav, SourceMark, u } from "@/components/ui";
import q from "./queue.module.css";

interface QueueItem {
  id: string;
  name: string;
  age: number;
  language: string;
  renewal_date: string;
  days_to_renewal: number;
  bucket: Bucket;
  fragile: boolean;
  status: CaseStatus;
  state_status: "compliant" | "exempt" | "not_determined";
  state_rule_ids: string[];
  verified_spans: number;
  clinician_name: string;
  top_missing_fact: { key: string; holder: Holder; database: string | null; why: string } | null;
}

interface Summary {
  cohort: number;
  a_exempt: number;
  a_not_determined: number;
  final_exempt?: number;
  one_away?: number;
  verifier_dropped?: number;
}

type PatientRunStatus = "pending" | "reading" | "complete" | "failed";
type PatientResult = {
  bucket: Bucket;
  fragile: boolean;
  case_status: CaseStatus;
  verified_spans: number;
  top_missing_fact: QueueItem["top_missing_fact"];
  notes: number;
  claims: number;
  kept: number;
  dropped: number;
  unlocatable: number;
};
type PatientRunState = { status: PatientRunStatus; result?: PatientResult; error?: string };
type LiveRun = {
  run_id: string;
  status: string;
  provider: string;
  patients: number;
  notes: number;
  completed: number;
  failed: number;
  elapsed_seconds: number;
  patient_states: Record<string, PatientRunState>;
};
type LiveEvent = {
  type: "patient" | "run";
  status: string;
  patient_id?: string;
  result?: PatientResult;
  error?: string;
  completed?: number;
  failed?: number;
  elapsed_seconds?: number;
};

const TABS: Array<Bucket | "ALL"> = ["ALL", "ONE_AWAY", "PROVABLE", "NO_PATH", "SAFE"];
const HOLDER_SOURCE = { patient: "patient_reply", database: "external_db", clinician: "clinician_attestation" } as const;

function RunMark({ status }: { status: PatientRunStatus }) {
  if (status === "complete") {
    return (
      <svg className={q.progressIcon} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="10" cy="10" r="7.5" />
        <path d="m6.5 10 2.2 2.2 4.8-5" />
      </svg>
    );
  }
  if (status === "failed") {
    return (
      <svg className={`${q.progressIcon} ${q.progressFailed}`} viewBox="0 0 20 20" fill="none"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
        <circle cx="10" cy="10" r="7.5" />
        <path d="m7.5 7.5 5 5m0-5-5 5" />
      </svg>
    );
  }
  return (
    <svg className={`${q.progressIcon} ${status === "reading" ? q.progressReading : ""}`} viewBox="0 0 20 20"
      fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
      <circle cx="10" cy="10" r="7.5" strokeDasharray={status === "reading" ? "28 20" : undefined} />
    </svg>
  );
}

export default function QueuePage() {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [tab, setTab] = useState<Bucket | "ALL">("ALL");
  const [soon, setSoon] = useState(true);
  const [state, setState] = useState<Summary | null>(null);
  const [evidence, setEvidence] = useState<Summary | null>(null);
  const [running, setRunning] = useState<"state" | "notes" | null>(null);
  const [liveRun, setLiveRun] = useState<LiveRun | null>(null);
  const [patientProgress, setPatientProgress] = useState<Record<string, PatientRunState>>({});
  const [runError, setRunError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const streamRef = useRef<EventSource | null>(null);

  const finishLiveRun = useCallback(async (event: LiveEvent) => {
    streamRef.current?.close();
    streamRef.current = null;
    setRunning(null);
    setLiveRun((current) => current ? {
      ...current,
      status: event.status,
      completed: event.completed ?? current.completed,
      failed: event.failed ?? current.failed,
      elapsed_seconds: event.elapsed_seconds ?? current.elapsed_seconds,
    } : current);
    const [queueResponse, summaryResponse] = await Promise.all([
      apiFetch("/api/queue"),
      apiFetch("/api/summary"),
    ]);
    if (queueResponse.ok) setItems(await queueResponse.json());
    if (summaryResponse.ok) setEvidence(await summaryResponse.json());
    if (event.status !== "complete") {
      setRunError(event.error ?? "Some charts could not be read. Completed patients remain available.");
    }
  }, []);

  const connectToRun = useCallback((runId: string) => {
    streamRef.current?.close();
    const source = new EventSource(apiUrl(`/api/run/evidence/stream?run_id=${encodeURIComponent(runId)}`));
    streamRef.current = source;
    source.onmessage = (message) => {
      const event: LiveEvent = JSON.parse(message.data);
      if (event.type === "patient" && event.patient_id) {
        const next: PatientRunState = { status: event.status as PatientRunStatus };
        if (event.result) next.result = event.result;
        if (event.error) next.error = event.error;
        setPatientProgress((current) => ({ ...current, [event.patient_id!]: next }));
        if (event.result) {
          setItems((current) => current?.map((item) => item.id === event.patient_id ? {
            ...item,
            bucket: event.result!.bucket,
            fragile: event.result!.fragile,
            status: event.result!.case_status,
            verified_spans: event.result!.verified_spans,
            top_missing_fact: event.result!.top_missing_fact,
          } : item) ?? null);
        }
      }
      if (event.type === "run" && event.status !== "running") {
        void finishLiveRun(event);
      }
    };
    source.onerror = () => {
      setRunError("The progress stream was interrupted. The server is still reading charts; refresh to reconnect.");
    };
  }, [finishLiveRun]);

  useEffect(() => {
    let active = true;
    async function hydrate() {
      const [queueResponse, statusResponse] = await Promise.all([
        apiFetch("/api/queue"),
        apiFetch("/api/run/evidence/status"),
      ]);
      if (!active) return;
      if (queueResponse.ok) setItems(await queueResponse.json());
      if (!statusResponse.ok) return;
      const job = await statusResponse.json();
      if (job.status === "idle") return;
      setLiveRun(job);
      setPatientProgress(job.patient_states ?? {});
      const summaryResponse = await apiFetch("/api/summary");
      if (!active || !summaryResponse.ok) return;
      const summary = await summaryResponse.json();
      setState(summary);
      if (job.status === "running") {
        setRunning("notes");
        connectToRun(job.run_id);
      } else {
        setEvidence(summary);
      }
    }
    void hydrate();
    return () => {
      active = false;
      streamRef.current?.close();
    };
  }, [connectToRun]);

  useEffect(() => {
    if (running !== "notes") {
      setElapsed(0);
      return;
    }
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  async function runState() {
    setRunning("state");
    setRunError(null);
    const response = await apiFetch("/api/run/state", { method: "POST" });
    if (response.ok) {
      setState(await response.json());
      setEvidence(null);
      setLiveRun(null);
      setPatientProgress({});
      setTab("ALL");
    } else {
      setRunError(`The state check failed (${response.status}).`);
    }
    setRunning(null);
  }

  async function readNotesLive() {
    setRunning("notes");
    setRunError(null);
    setEvidence(null);
    setTab("ALL");
    setSoon(false);
    try {
      const response = await apiFetch("/api/run/evidence", { method: "POST" });
      const job = await response.json();
      if (!response.ok) throw new Error(job.detail ?? `The live run failed (${response.status}).`);
      setLiveRun(job);
      setPatientProgress(job.patient_states);
      connectToRun(job.run_id);
    } catch (error) {
      setRunning(null);
      setRunError(error instanceof Error ? error.message : "The live run could not start.");
    }
  }

  const inWindow = useMemo(() => (items ?? []).filter((i) => !soon || i.days_to_renewal <= 30), [items, soon]);
  const counts = useMemo(() => {
    const c: Record<string, number> = { ALL: inWindow.length };
    for (const i of inWindow) c[i.bucket] = (c[i.bucket] ?? 0) + 1;
    return c;
  }, [inWindow]);
  const rows = !evidence || tab === "ALL" ? inWindow : inWindow.filter((i) => i.bucket === tab);
  const completed = Object.values(patientProgress).filter((item) => item.status === "complete").length;
  const reading = Object.values(patientProgress).filter((item) => item.status === "reading").length;

  return (
    <>
      <Nav />
      <main className={u.wrap}>
        <section className={q.head}>
          <p className={u.eyebrow}>Work queue · As of February 15, 2027</p>
          <h1 className={u.h1}>Your clinic&apos;s patients, before their renewal.</h1>
          <p className={q.lede}>
            Everyone whose Medicaid coverage comes up for renewal, what the state&apos;s check will conclude, and for
            each person it cannot clear, the one action that keeps them covered.
          </p>
        </section>

        <p className={q.revealNote}>
          Nothing from the charts is revealed in advance. Run the state&apos;s check, then watch the evidence finder and
          verifier read each patient&apos;s notes live.
        </p>
        <section className={q.reveal} aria-label="The state's check, then the notes">
          <div className={`${q.step} ${q.stepState}`}>
            <div className={q.stepTop}>
              <span className={q.stepTitle}><span className={q.stepNum}>1.</span>What the state sees</span>
              <button className={state ? u.btnQuiet : u.btnDark} onClick={runState} disabled={running !== null}>
                {running === "state" ? "Running…" : state ? "Run the state check again" : "Run the state's check →"}
              </button>
            </div>
            <p className={q.help}>
              The state&apos;s own check, as it runs today, over every member: primary billing codes and the
              state&apos;s own databases only. Nothing from clinical notes.
            </p>
            <div className={q.counters} aria-live="polite">
              <div className={q.counter}>
                <div className={`${q.big} ${state ? "" : q.bigDim}`}><AnimatedNumber value={state?.cohort ?? null} /></div>
                <div className={q.counterLabel}>Patients renewing</div>
              </div>
              <div className={q.counter}>
                <div className={`${q.big} ${state ? "" : q.bigDim}`}><AnimatedNumber value={state?.a_exempt ?? null} /></div>
                <div className={q.counterLabel}>State clears</div>
              </div>
              <div className={q.counter}>
                <div className={`${q.big} ${state ? "" : q.bigDim}`}><AnimatedNumber value={state?.a_not_determined ?? null} /></div>
                <div className={q.counterLabel}>State can&apos;t determine</div>
              </div>
            </div>
          </div>

          <div className={`${q.step} ${q.stepNotes}`}>
            <div className={q.stepTop}>
              <span className={q.stepTitle}><span className={q.stepNum}>2.</span>What the chart says</span>
              <button className={evidence ? u.btnQuiet : u.btnDark} onClick={readNotesLive}
                disabled={running !== null || !state}>
                {running === "notes" ? "Reading live…" : evidence ? "Read the charts again" : "Read the charts live →"}
              </button>
            </div>
            <p className={q.help}>
              Starts the evidence finder and independent verifier now. Each patient becomes available as soon as both
              models finish that chart; the rest continue in the background.
            </p>
            {running === "notes" && liveRun ? (
              <div className={q.liveProgress} role="status" aria-live="polite">
                <div><strong>{completed}</strong><span>of {liveRun.patients} charts ready</span></div>
                <div><strong>{reading}</strong><span>being read now</span></div>
                <div>
                  <strong>{elapsed}s</strong>
                  <span>{liveRun.provider === "bedrock" ? "Amazon Bedrock" : "OpenAI fallback"} live run</span>
                </div>
              </div>
            ) : (
              <div className={q.counters} aria-live="polite">
              <div className={q.counter}>
                <div className={`${q.big} ${evidence ? "" : q.bigDim}`}><AnimatedNumber value={evidence?.final_exempt ?? null} /></div>
                <div className={q.counterLabel}>Clear once notes are read</div>
              </div>
              <div className={q.counter}>
                <div className={`${q.big} ${evidence ? "" : q.bigDim}`}>
                  <AnimatedNumber value={evidence && state ? (evidence.final_exempt ?? 0) - state.a_exempt : null} />
                </div>
                <div className={q.counterLabel}>Already qualify, state misses</div>
              </div>
              <div className={q.counter}>
                <div className={`${q.big} ${evidence ? "" : q.bigDim}`}><AnimatedNumber value={evidence?.one_away ?? null} /></div>
                <div className={q.counterLabel}>One fact away</div>
              </div>
              </div>
            )}
            {evidence && (
              <div className={q.runReceipt}>
                <SourceMark source="note_span" />
                <span>
                  <strong>
                    {liveRun?.notes ?? 0} notes read live with {
                      liveRun?.provider === "bedrock" ? "Amazon Bedrock" : "the OpenAI fallback"
                    } in {liveRun?.elapsed_seconds ?? 0}s
                  </strong>
                  Independent verifier dropped {evidence.verifier_dropped} claims the quoted text did not support
                </span>
              </div>
            )}
            {runError && <p className={q.runError} role="alert">{runError}</p>}
          </div>
        </section>

        <section aria-label="Queue">
          <div className={q.filters}>
            {evidence ? (
              <div className={q.tabs} role="group" aria-label="Filter by bucket">
                {TABS.map((t) => (
                  <button key={t} className={q.tab} aria-pressed={tab === t} onClick={() => setTab(t)}>
                    {t === "ALL" ? "All" : BUCKET_LABEL[t]}<span className={q.count}>{counts[t] ?? 0}</span>
                  </button>
                ))}
              </div>
            ) : (
              <span className={q.filterWaiting}>
                {running === "notes" ? `${completed} charts ready; bucket filters appear when the run finishes.` :
                  "Bucket filters appear after the charts are read."}
              </span>
            )}
            <label className={q.toggle}>
              <input type="checkbox" checked={soon} onChange={(e) => setSoon(e.target.checked)} />
              Renewing in 30 days
            </label>
          </div>

          {items === null ? (
            <p className={u.muted}>Loading the queue…</p>
          ) : (
            <div className={q.tableWrap}>
              <table className={q.table}>
                <thead>
                  <tr>
                    <th scope="col">Patient</th>
                    <th scope="col">Renewal</th>
                    <th scope="col">State check</th>
                    <th scope="col">After chart read</th>
                    <th scope="col">Next step</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((i) => (
                    <tr key={i.id} className={
                      patientProgress[i.id]?.status === "complete" && i.bucket === "SAFE" ? q.quiet : undefined
                    }>
                      <td>
                        {patientProgress[i.id]?.status === "complete" ? (
                          <Link className={q.name} href={`/cases/${i.id}`}>{i.name}</Link>
                        ) : (
                          <span className={q.nameLocked}>{i.name}</span>
                        )}
                        <span className={q.sub}>{i.age} · {LANGUAGE_LABEL[i.language] ?? i.language}</span>
                      </td>
                      <td>
                        {i.renewal_date}
                        <span className={`${q.sub} ${
                          i.days_to_renewal <= 14
                          && (patientProgress[i.id]?.status === "complete"
                            ? i.bucket !== "SAFE"
                            : i.state_status === "not_determined")
                            ? q.soon : ""
                        }`}>
                          in {i.days_to_renewal} day{i.days_to_renewal === 1 ? "" : "s"}
                        </span>
                      </td>
                      <td>
                        {!state ? (
                          <span className={u.faint}>Not checked</span>
                        ) : i.state_status === "not_determined" ? (
                          <span className={q.stateResult}>
                            <strong>Cannot determine</strong>
                            <span>Would send a notice</span>
                          </span>
                        ) : (
                          <span className={`${q.stateResult} ${q.stateCleared}`}>
                            <strong>State clears</strong>
                            <span>{i.state_status === "exempt" ? "Exempt" : "Meets requirement"}</span>
                          </span>
                        )}
                      </td>
                      <td>
                        {(() => {
                          const progress = patientProgress[i.id]?.status ?? (evidence ? "complete" : "pending");
                          return (
                            <span className={`${q.progressCell} ${q[`progress-${progress}`]}`}>
                              <RunMark status={progress} />
                              <span>
                                {progress === "complete" ? (
                                  <>
                                    <BucketTag bucket={i.bucket} />
                                    <span className={q.sub}>{i.verified_spans} verified span{i.verified_spans === 1 ? "" : "s"}</span>
                                  </>
                                ) : (
                                  <>
                                    <strong>{progress === "reading" ? "Reading chart…" : progress === "failed" ? "Read failed" : "Waiting for live read"}</strong>
                                    {progress === "reading" && <span className={q.sub}>Evidence finder + verifier</span>}
                                  </>
                                )}
                              </span>
                            </span>
                          );
                        })()}
                        {patientProgress[i.id]?.status === "complete" && i.fragile && (
                          <span className={q.sub}><span className={u.fragile}>▲ Fragile basis</span></span>
                        )}
                      </td>
                      <td title={patientProgress[i.id]?.status === "complete" ? i.top_missing_fact?.why : undefined}>
                        {patientProgress[i.id]?.status === "complete" && i.top_missing_fact ? (
                          <span className={q.next}>
                            <SourceMark source={HOLDER_SOURCE[i.top_missing_fact.holder]} />
                            {nextStepShort(i.top_missing_fact, i.name, i.clinician_name)}
                          </span>
                        ) : patientProgress[i.id]?.status === "complete" ? (
                          <span className={u.faint}>{i.bucket === "SAFE" ? "Nothing to do" : "Help reporting hours"}</span>
                        ) : (
                          <span className={u.faint}>Available after chart read</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
      <Footer />
    </>
  );
}
