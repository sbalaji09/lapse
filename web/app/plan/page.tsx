"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
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

export default function PlanPage() {
  const [s, setS] = useState<Summary | null>(null);
  const [fragile, setFragile] = useState<FragileRow[]>([]);
  const [noContact, setNoContact] = useState<number | null>(null);

  useEffect(() => {
    apiFetch("/api/summary").then((r) => r.json()).then(setS);
    apiFetch("/api/eval").then((r) => r.json()).then((e) => {
      setFragile(e.fragile ?? []);
      setNoContact(e.pitch?.database_resolved_no_contact ?? null);
    });
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
      </main>
      <Footer />
    </>
  );
}
