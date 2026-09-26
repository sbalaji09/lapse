"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { firstName } from "@/components/labels";
import { SourceMark, u } from "@/components/ui";
import k from "./clinician.module.css";

interface Span {
  source: "note_span" | "patient_reply";
  quote: string;
  context?: string;
  date: string | null;
  label?: string;
  author?: string | null;
}

interface CardData {
  patient_id: string;
  name: string;
  status: string;
  clinician_name: string;
  question: string;
  condition: string;
  spans: Span[];
  attesting_to: string;
}

/** The full sentence, with the exact words the fact came from highlighted. */
function withHighlight(text: string, quote: string) {
  const at = text.indexOf(quote);
  if (at < 0) return <mark>{text}</mark>;
  return <>{text.slice(0, at)}<mark>{quote}</mark>{text.slice(at + quote.length)}</>;
}

export default function ClinicianCard() {
  const { token } = useParams<{ token: string }>();
  const [card, setCard] = useState<CardData | null>(null);
  const [missing, setMissing] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiFetch(`/api/clinician/${token}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setCard)
      .catch(() => setMissing(true));
  }, [token]);

  async function decide(decision: "sign" | "decline") {
    setBusy(true);
    try {
      const res = await apiFetch(`/api/clinician/${token}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
      setResult((await res.json()).status);
    } finally {
      setBusy(false);
    }
  }

  if (missing) return <main className={k.page}><div className={k.card}>This link is not valid.</div></main>;
  if (!card) return <main className={k.page}><div className={k.card}>Loading…</div></main>;

  const already = card.status === "attestation_ready";

  return (
    <main className={k.page}>
      <section className={k.card} aria-labelledby="q">
        <div className={k.top}>
          <span>{card.clinician_name} · attestation request</span>
          <span className={u.synthetic}>Synthetic data</span>
        </div>
        <p className={u.eyebrow}>{card.name}</p>

        {result || already ? (
          <>
            <p className={k.done}>
              {result === "needs_action" ? "Declined. The clinic has been told." : "Signed. Thank you."}
            </p>
            <p className={k.fine}>
              {result === "needs_action"
                ? `${firstName(card.name)}'s case goes back to the enrollment worker.`
                : "The medical exemption attestation is ready for the clinic to submit."}
            </p>
          </>
        ) : (
          <>
            <h1 id="q" className={k.question}>{card.question}</h1>
            <ul className={k.spans}>
              {card.spans.map((s, i) => (
                <li key={i} className={k.span}>
                  <SourceMark source={s.source} />
                  <div>
                    <div className={k.spanLabel}>
                      {s.source === "patient_reply" ? s.label : `Clinical note · ${s.date}${s.author ? ` · ${s.author}` : ""}`}
                    </div>
                    <p className={k.quote}>{withHighlight(s.context ?? s.quote, s.quote)}</p>
                  </div>
                </li>
              ))}
            </ul>
            <div className={k.buttons}>
              <button className={u.btnDark} disabled={busy} onClick={() => decide("sign")}>Sign attestation</button>
              <button className={u.btn} disabled={busy} onClick={() => decide("decline")}>Decline</button>
            </div>
            <p className={k.fine}>{card.attesting_to}</p>
          </>
        )}
      </section>
    </main>
  );
}
