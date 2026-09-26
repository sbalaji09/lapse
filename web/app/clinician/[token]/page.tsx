"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { apiFetch } from "@/lib/api";

type ClinicianCase = {
  patient_id: string;
  name: string;
  status: string;
};

export default function ClinicianPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;

  const [data, setData] = useState<ClinicianCase | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    apiFetch(`/api/clinician/${token}`)
      .then((res) => res.json())
      .then(setData)
      .catch(() => setData(null));
  }, [token]);

  async function decide(decision: "sign" | "decline") {
    setLoading(true);
    try {
      const res = await apiFetch(`/api/clinician/${token}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
      const json = await res.json();
      setResult(json.status);
    } finally {
      setLoading(false);
    }
  }

  if (!data) {
    return <div>Loading...</div>;
  }

  return (
    <div>
      <h1>{data.name}</h1>
      {result ? (
        <p>Recorded: {result}</p>
      ) : (
        <>
          <p>
            Does the record support this patient&apos;s condition limits their
            ability to work?
          </p>
          <button disabled={loading} onClick={() => decide("sign")}>
            Sign attestation
          </button>
          <button disabled={loading} onClick={() => decide("decline")}>
            Decline
          </button>
          <p>
            You&apos;re attesting to what the record shows. The state makes
            the eligibility decision.
          </p>
        </>
      )}
    </div>
  );
}
