"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { Bucket } from "@/lib/types";
import { BucketTag, Footer, Nav, u } from "@/components/ui";
import p from "./plan.module.css";

interface Summary {
  cohort: number;
  a_exempt: number;
  a_not_determined: number;
  final_exempt: number;
  provable: number;
  one_away: number;
  no_path: number;
  fragile: number;
  recovered: number;
}

interface FragileRow {
  patient_id: string;
  name: string;
  renewal_date: string;
  state_code: string | null;
  state_code_display: string | null;
  finding: string;
}

interface PRF { p: number; r: number; f1: number }
interface Confusion { tp: number; fp: number; fn: number; tn: number }
interface EvalData {
  label: string;
  patient_level: { a: PRF; final: PRF; after_outreach: PRF & { note: string }; confusion: Record<string, Confusion>; truly_qualify: number };
  claim_level: PRF & { impairment: PRF & { predicted: number; labels: number }; condition: PRF & { predicted: number; labels: number } };
  verifier: { kept: number; dropped: number; dropped_correctly: number; dropped_real_evidence: number;
              extraction_precision_before_verifier: number; precision_after_verifier: number };
}

// Series follow the entity, never the rank. Colors validated against --paper (dataviz validator: all checks pass).
const SERIES = [
  { key: "a", label: "State's check", color: "var(--series-state)", hatch: false },
  { key: "final", label: "Lapse, before any outreach", color: "var(--series-lapse)", hatch: false },
  { key: "after_outreach", label: "Lapse, after outreach (simulated)", color: "var(--series-lapse)", hatch: true },
] as const;

type Tip = { x: number; y: number; text: string } | null;

function Swatch({ color, hatch }: { color: string; hatch: boolean }) {
  return (
    <svg width="16" height="12" aria-hidden="true">
      <rect x="0.5" y="0.5" width="15" height="11" rx="3" fill={hatch ? "url(#hatch)" : color} stroke={color} />
    </svg>
  );
}

function BarPanel({ title, sub, measure, data, onTip }: {
  title: string; sub: string; measure: "r" | "p"; data: EvalData;
  onTip: (clientX: number, clientY: number, text: string | null) => void;
}) {
  const W = 520, rowH = 44, left = 0, labelW = 0, top = 8, plotW = W - 60;
  const H = top + SERIES.length * rowH + 26;
  const conf = data.patient_level.confusion;
  return (
    <div>
      <h3 className={p.panelTitle}>{title}</h3>
      <p className={p.panelSub}>{sub}</p>
      <svg className={p.svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title} by method`}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={left + labelW + t * plotW} x2={left + labelW + t * plotW} y1={top} y2={H - 22}
              stroke="rgba(30,30,30,0.1)" strokeWidth="1" />
            <text className={p.axisText} x={left + labelW + t * plotW} y={H - 6} textAnchor="middle">{t.toFixed(2)}</text>
          </g>
        ))}
        {SERIES.map((s, i) => {
          const v = (data.patient_level as any)[s.key][measure] as number;
          const y = top + i * rowH;
          const w = Math.max(2, v * plotW);
          const c = conf[s.key];
          const tip = `${s.label}: ${measure === "r" ? "recall" : "precision"} ${v.toFixed(3)} · cleared ${c.tp + c.fp}, of whom ${c.tp} truly qualify; missed ${c.fn}`;
          return (
            <g key={s.key}>
              <text className={p.catText} x={labelW} y={y + 11}>{s.label}</text>
              <path d={`M${labelW} ${y + 18} h${w - 4} a4 4 0 0 1 4 4 v8 a4 4 0 0 1 -4 4 h${-(w - 4)} z`}
                fill={s.hatch ? "url(#hatch)" : s.color} stroke={s.color} strokeWidth={s.hatch ? 1.5 : 0} />
              <text className={p.valueText} x={labelW + w + 8} y={y + 32}>{v.toFixed(2)}</text>
              {/* hit target larger than the mark */}
              <rect x={0} y={y} width={W} height={rowH} fill="transparent"
                onMouseMove={(ev) => onTip(ev.clientX, ev.clientY, tip)}
                onMouseLeave={() => onTip(0, 0, null)} />
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function Matrix({ title, c, note }: { title: string; c: Confusion; note?: string }) {
  return (
    <div className={p.matrix}>
      <p className={p.matrixTitle}>{title}</p>
      <table className={p.cm}>
        <thead>
          <tr><th /><th scope="col">Truly qualify</th><th scope="col">Do not</th></tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Cleared</th>
            <td className={p.cmGood}>{c.tp}</td>
            <td className={p.cmPlain}>{c.fp}</td>
          </tr>
          <tr>
            <th scope="row">Not cleared</th>
            <td className={p.cmMiss}>{c.fn}<span className={p.cmNote}>would get a notice</span></td>
            <td className={p.cmPlain}>{c.tn}</td>
          </tr>
        </tbody>
      </table>
      {note && <p className={u.faint} style={{ margin: "8px 0 0" }}>{note}</p>}
    </div>
  );
}

export default function PlanPage() {
  const [s, setS] = useState<Summary | null>(null);
  const [fragile, setFragile] = useState<FragileRow[]>([]);
  const [noContact, setNoContact] = useState<number | null>(null);

  const [data, setData] = useState<EvalData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tip, setTip] = useState<Tip>(null);
  const cardRef = useRef<HTMLElement>(null);
  const showTip = (x: number, y: number, text: string | null) => {
    const box = cardRef.current?.getBoundingClientRect();
    setTip(text && box ? { x: x - box.left, y: y - box.top, text } : null);
  };

  useEffect(() => {
    apiFetch("/api/summary").then((r) => r.json()).then(setS);
    apiFetch("/api/eval").then((r) => r.json()).then((e) => {
      setFragile(e.fragile ?? []);
      setNoContact(e.pitch?.database_resolved_no_contact ?? null);
    });
    apiFetch("/api/eval")
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setData)
      .catch(() => setError("No evaluation yet. Run `make eval`."));
  }, []);

  const safe = s ? s.cohort - s.provable - s.one_away - s.no_path : 0;
  const rows: Array<[Bucket, number]> = s
    ? [["ONE_AWAY", s.one_away], ["PROVABLE", s.provable], ["NO_PATH", s.no_path], ["SAFE", safe]]
    : [];

  return (
    <>
      <Nav />
      <main className={u.wrap}>
        <section className={p.head}>
          <p className={u.eyebrow}>For health plans</p>
          <h1 className={u.h1}>Members you would lose to paperwork.</h1>
          <p className={p.lede}>
            Across the clinics&apos; {s?.cohort.toLocaleString() ?? "…"} members renewing in the next six months: who the
            state&apos;s check cannot clear, who Lapse clears from records they already have, and which exemptions would not
            survive an audit.
          </p>
        </section>

        <section className={p.tiles} aria-label="Totals">
          <div className={`${p.tile} ${p.t0}`}>
            <div className={p.tileLabel}>At risk</div>
            <div className={p.tileValue}>{s?.a_not_determined ?? "–"}</div>
            <div className={p.tileNote}>The state&apos;s check cannot determine them</div>
          </div>
          <div className={`${p.tile} ${p.t1}`}>
            <div className={p.tileLabel}>Recovered</div>
            <div className={p.tileValue}>{s?.recovered ?? "–"}</div>
            <div className={p.tileNote}>Cleared from the chart and databases</div>
          </div>
          <div className={`${p.tile} ${p.t2}`}>
            <div className={p.tileLabel}>Nobody contacted</div>
            <div className={p.tileValue}>{noContact ?? "–"}</div>
            <div className={p.tileNote}>Resolved by a database lookup alone</div>
          </div>
          <div className={`${p.tile} ${p.t3}`}>
            <div className={p.tileLabel}>Fragile exemptions</div>
            <div className={p.tileValue}>{s?.fragile ?? "–"}</div>
            <div className={p.tileNote}>Exempt on a code the chart does not support</div>
          </div>
        </section>

        <div className={p.grid}>
          <section aria-labelledby="buckets">
            <h2 id="buckets" className={u.h2}>Where everyone stands</h2>
            <ul className={p.buckets}>
              {rows.map(([b, n]) => (
                <li key={b} className={p.bucketRow}>
                  <BucketTag bucket={b} />
                  <span className={p.bucketCount}>{n.toLocaleString()}</span>
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="fragile">
            <h2 id="fragile" className={u.h2}>The fragile list</h2>
            <p className={u.muted} style={{ marginTop: -6 }}>
              The state exempts these members on a frailty billing code, and not one sentence in their notes supports it.
            </p>
            <table className={p.table}>
              <thead>
                <tr>
                  <th scope="col">Member</th>
                  <th scope="col">Renews</th>
                  <th scope="col">Code the state used</th>
                  <th scope="col">What the chart shows</th>
                </tr>
              </thead>
              <tbody>
                {fragile.map((f) => (
                  <tr key={f.patient_id}>
                    <td><Link href={`/cases/${f.patient_id}`}>{f.name}</Link></td>
                    <td>{f.renewal_date}</td>
                    <td>{f.state_code_display}<span className={p.code}>{f.state_code}</span></td>
                    <td>{f.finding}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>

        <h2 className={u.h2} style={{ marginTop: 48 }}>Accuracy, measured</h2>
        <section className={p.evalHead}>
          <p className={u.eyebrow}>Accuracy</p>
          <h1 className={u.h1}>Who the state misses, measured.</h1>
          {data && <span className={p.evalLabel}>{data.label}</span>}
        </section>
        {error && <p className={u.muted}>{error}</p>}
        {data && (
          <>
            <section className={p.chartCard} ref={cardRef} aria-labelledby="chart-title">
              <h2 id="chart-title" className={u.visuallyHidden}>Recall and precision by method</h2>
              <svg width="0" height="0" aria-hidden="true" style={{ position: "absolute" }}>
                <defs>
                  <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                    <rect width="6" height="6" fill="var(--paper)" />
                    <line x1="0" y1="0" x2="0" y2="6" stroke="var(--series-lapse)" strokeWidth="3" />
                  </pattern>
                </defs>
              </svg>
              <ul className={p.legend}>
                {SERIES.map((s) => (
                  <li key={s.key}><Swatch color={s.color} hatch={s.hatch} />{s.label}</li>
                ))}
              </ul>
              <div className={p.panels}>
                <BarPanel title="Recall" sub={`Of the ${data.patient_level.truly_qualify} people who truly qualify, the share each method clears.`}
                  measure="r" data={data} onTip={showTip} />
                <BarPanel title="Precision" sub="Of the people each method clears, the share who truly qualify."
                  measure="p" data={data} onTip={showTip} />
              </div>
              {tip && <div className={p.tooltip} style={{ left: tip.x, top: tip.y }}>{tip.text}</div>}
              <details className={p.tableToggle}>
                <summary>Show as a table</summary>
                <table className={p.dataTable}>
                  <thead><tr><th scope="col">Method</th><th scope="col">Recall</th><th scope="col">Precision</th><th scope="col">F1</th></tr></thead>
                  <tbody>
                    {SERIES.map((s) => {
                      const m = (data.patient_level as any)[s.key] as PRF;
                      return <tr key={s.key}><td>{s.label}</td><td>{m.r.toFixed(3)}</td><td>{m.p.toFixed(3)}</td><td>{m.f1.toFixed(3)}</td></tr>;
                    })}
                  </tbody>
                </table>
              </details>
            </section>

            <h2 className={u.h2}>Who each method clears</h2>
            <div className={p.row3}>
              <Matrix title="State's check" c={data.patient_level.confusion.a} />
              <Matrix title="Lapse, before any outreach" c={data.patient_level.confusion.final} />
              <Matrix title="Lapse, after outreach" c={data.patient_level.confusion.after_outreach}
                note="Simulated: every one-fact-away question answered from the synthetic truth." />
            </div>

            <div className={p.two}>
              <section className={p.matrix} aria-labelledby="verifier">
                <h2 id="verifier" className={p.matrixTitle}>The verifier</h2>
                <div className={p.stat}>{data.verifier.dropped}</div>
                <p className={u.muted} style={{ margin: 0 }}>
                  claims dropped of {data.verifier.kept + data.verifier.dropped}; {data.verifier.dropped_correctly} of them were
                  not real evidence (a relative, a resolved problem, remote history). Claim precision
                  {" "}{data.verifier.extraction_precision_before_verifier.toFixed(3)} before the verifier,
                  {" "}{data.verifier.precision_after_verifier.toFixed(3)} after.
                </p>
              </section>
              <section className={p.matrix} aria-labelledby="claims">
                <h2 id="claims" className={p.matrixTitle}>Finding the sentence</h2>
                <table className={p.dataTable}>
                  <thead><tr><th scope="col">Evidence</th><th scope="col">Precision</th><th scope="col">Recall</th><th scope="col">F1</th></tr></thead>
                  <tbody>
                    <tr><td>States a limitation</td><td>{data.claim_level.impairment.p.toFixed(3)}</td>
                      <td>{data.claim_level.impairment.r.toFixed(3)}</td><td>{data.claim_level.impairment.f1.toFixed(3)}</td></tr>
                    <tr><td>Names a qualifying condition</td><td>{data.claim_level.condition.p.toFixed(3)}</td>
                      <td>{data.claim_level.condition.r.toFixed(3)}</td><td>{data.claim_level.condition.f1.toFixed(3)}</td></tr>
                  </tbody>
                </table>
              </section>
            </div>
          </>
        )}
      </main>
      <Footer />
    </>
  );
}
