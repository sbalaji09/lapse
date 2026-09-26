"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { Bucket, CaseStatus, Holder } from "@/lib/types";
import { BUCKET_LABEL, LANGUAGE_LABEL, STATUS_LABEL, nextStepShort } from "@/components/labels";
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

const TABS: Array<Bucket | "ALL"> = ["ALL", "ONE_AWAY", "PROVABLE", "NO_PATH", "SAFE"];
const HOLDER_SOURCE = { patient: "patient_reply", database: "external_db", clinician: "clinician_attestation" } as const;

export default function QueuePage() {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [tab, setTab] = useState<Bucket | "ALL">("ALL");
  const [soon, setSoon] = useState(true);
  const [state, setState] = useState<Summary | null>(null);
  const [evidence, setEvidence] = useState<Summary | null>(null);
  const [running, setRunning] = useState<"state" | "notes" | null>(null);

  useEffect(() => {
    apiFetch("/api/queue").then((r) => r.json()).then(setItems);
  }, []);

  async function run(which: "state" | "notes") {
    setRunning(which);
    const res = await apiFetch(which === "state" ? "/api/run/state" : "/api/run/evidence", { method: "POST" });
    const data = await res.json();
    which === "state" ? setState(data) : setEvidence(data);
    setRunning(null);
  }

  const inWindow = useMemo(() => (items ?? []).filter((i) => !soon || i.days_to_renewal <= 30), [items, soon]);
  const counts = useMemo(() => {
    const c: Record<string, number> = { ALL: inWindow.length };
    for (const i of inWindow) c[i.bucket] = (c[i.bucket] ?? 0) + 1;
    return c;
  }, [inWindow]);
  const rows = tab === "ALL" ? inWindow : inWindow.filter((i) => i.bucket === tab);

  return (
    <>
      <Nav />
      <main className={u.wrap}>
        <section className={q.head}>
          <p className={u.eyebrow}>Morning queue · Feb 15, 2027</p>
          <h1 className={u.h1}>Your clinic&apos;s patients, before their renewal.</h1>
        </section>

        <section className={q.reveal} aria-label="The state's check, then the notes">
          <div className={`${q.step} ${q.stepState}`}>
            <div className={q.stepTop}>
              <span className={q.stepTitle}><span className={q.stepNum}>1.</span>What the state sees</span>
              <button className={state ? u.btnQuiet : u.btnDark} onClick={() => run("state")} disabled={running !== null}>
                {running === "state" ? "Running…" : state ? "Run again" : "Run the state's check →"}
              </button>
            </div>
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
              <button className={evidence ? u.btnQuiet : u.btnDark} onClick={() => run("notes")}
                disabled={running !== null || !state}>
                {running === "notes" ? "Reading…" : evidence ? "Read again" : "Read the notes →"}
              </button>
            </div>
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
            {evidence && (
              <span className={q.dropped}>
                <SourceMark source="note_span" />
                Verifier dropped {evidence.verifier_dropped} claims the quoted text did not support
              </span>
            )}
          </div>
        </section>

        <section aria-label="Queue">
          <div className={q.filters}>
            <div className={q.tabs} role="group" aria-label="Filter by bucket">
              {TABS.map((t) => (
                <button key={t} className={q.tab} aria-pressed={tab === t} onClick={() => setTab(t)}>
                  {t === "ALL" ? "All" : BUCKET_LABEL[t]}<span className={q.count}>{counts[t] ?? 0}</span>
                </button>
              ))}
            </div>
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
                    <th scope="col">Bucket</th>
                    <th scope="col">Status</th>
                    <th scope="col">Next step</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((i) => (
                    <tr key={i.id} className={i.bucket === "SAFE" ? q.quiet : undefined}>
                      <td>
                        <Link className={q.name} href={`/cases/${i.id}`}>{i.name}</Link>
                        <span className={q.sub}>{i.age} · {LANGUAGE_LABEL[i.language] ?? i.language}</span>
                      </td>
                      <td>
                        {i.renewal_date}
                        <span className={`${q.sub} ${i.days_to_renewal <= 14 && i.bucket !== "SAFE" ? q.soon : ""}`}>
                          in {i.days_to_renewal} day{i.days_to_renewal === 1 ? "" : "s"}
                        </span>
                      </td>
                      <td>
                        <BucketTag bucket={i.bucket} />
                        {i.fragile && <span className={q.sub}><span className={u.fragile}>▲ Fragile basis</span></span>}
                      </td>
                      <td>{STATUS_LABEL[i.status]}</td>
                      <td title={i.top_missing_fact?.why}>
                        {i.top_missing_fact ? (
                          <span className={q.next}>
                            <SourceMark source={HOLDER_SOURCE[i.top_missing_fact.holder]} />
                            {nextStepShort(i.top_missing_fact, i.name, i.clinician_name)}
                          </span>
                        ) : (
                          <span className={u.faint}>{i.bucket === "SAFE" ? "Nothing to do" : "Help reporting hours"}</span>
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
