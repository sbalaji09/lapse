"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { apiFetch } from "@/lib/api";
import type { Case, Note } from "@/lib/types";

type CaseWithNotes = Case & { notes: Note[] };

export default function CaseDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [data, setData] = useState<CaseWithNotes | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    apiFetch(`/api/cases/${id}`)
      .then((res) => res.json())
      .then((d) => setData(d))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading || !data) {
    return <div style={{ padding: 16 }}>Loading…</div>;
  }

  const dxRows = data.billed_dx_12mo as Array<{
    date: string;
    code: string;
    display: string;
    sequence: number;
  }>;
  const eventRows = data.events as Array<{ at: string; kind: string; detail: string }>;

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1>{data.display_name}</h1>
          <p>
            Bucket: <strong>{data.bucket}</strong> · Status: <strong>{data.status}</strong> ·
            Renewal: <strong>{data.renewal_date}</strong>
          </p>
        </div>
        <button onClick={load}>Refresh</button>
      </div>

      <div style={{ display: "flex", gap: 24, marginTop: 16 }}>
        <div style={{ flex: 1 }}>
          <h2>What the state sees</h2>
          <ul>
            {dxRows.map((dx, i) => (
              <li
                key={i}
                style={
                  dx.sequence === 1
                    ? { fontWeight: "bold" }
                    : { color: "#888" }
                }
              >
                {dx.date} — {dx.code} {dx.display}
                {dx.sequence !== 1 ? " (not read by the state)" : ""}
              </li>
            ))}
          </ul>
        </div>

        <div style={{ flex: 1 }}>
          <h2>What the chart says</h2>
          <ul>
            {data.claims.map((c) => (
              <li key={c.id}>
                <strong>{c.condition}</strong>: &ldquo;{c.quote}&rdquo;{" "}
                {c.verified === null ? "" : c.verified ? "(verified)" : "(not verified)"}
              </li>
            ))}
          </ul>
          {data.dropped_claims.length > 0 && (
            <details>
              <summary>Dropped claims ({data.dropped_claims.length})</summary>
              <ul>
                {data.dropped_claims.map((c) => (
                  <li key={c.id} style={{ textDecoration: "line-through", color: "#888" }}>
                    {c.condition}: &ldquo;{c.quote}&rdquo; — {c.verifier_reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>

        <div style={{ flex: 1 }}>
          <h2>What&apos;s missing</h2>
          {data.missing.map((m) => (
            <div
              key={m.id}
              style={{ border: "1px solid #ccc", padding: 8, marginBottom: 8 }}
            >
              <div>
                <strong>{m.key}</strong> ({m.holder})
              </div>
              <div>{m.why}</div>
              <div>Status: {m.status}</div>
            </div>
          ))}

          <h3>Evidence log</h3>
          <ul>
            {data.facts.map((f) => (
              <li key={f.id}>
                {f.key} = {String(f.value)} — {f.source}
                {f.quote ? ` ("${f.quote}")` : ""} @ {f.recorded_at}
              </li>
            ))}
          </ul>

          <h3>Timeline</h3>
          <ul>
            {eventRows.map((e, i) => (
              <li key={i}>
                {e.at} — {e.kind}: {e.detail}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
