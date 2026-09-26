"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import type { Bucket, CaseStatus } from "@/lib/types";

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
  top_missing_fact: { key: string; holder: string; why: string } | null;
}

const BUCKETS: Array<Bucket | "All"> = ["All", "SAFE", "PROVABLE", "ONE_AWAY", "NO_PATH"];

export default function QueuePage() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [bucket, setBucket] = useState<Bucket | "All">("All");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const path = bucket === "All" ? "/api/queue" : `/api/queue?bucket=${bucket}`;
    apiFetch(path)
      .then((res) => res.json())
      .then((data) => setItems(data))
      .finally(() => setLoading(false));
  }, [bucket]);

  return (
    <div style={{ padding: 16 }}>
      <h1>Queue</h1>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {BUCKETS.map((b) => (
          <button
            key={b}
            onClick={() => setBucket(b)}
            style={{ fontWeight: b === bucket ? "bold" : "normal" }}
          >
            {b}
          </button>
        ))}
      </div>
      {loading ? (
        <p>Loading…</p>
      ) : (
        <table border={1} cellPadding={4} style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr>
              <th>Name</th>
              <th>Age</th>
              <th>Language</th>
              <th>Renewal date</th>
              <th>Days to renewal</th>
              <th>Bucket</th>
              <th>Fragile</th>
              <th>Status</th>
              <th>Top missing fact</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>
                  <Link href={`/cases/${item.id}`}>{item.name}</Link>
                </td>
                <td>{item.age}</td>
                <td>{item.language}</td>
                <td>{item.renewal_date}</td>
                <td>{item.days_to_renewal}</td>
                <td>{item.bucket}</td>
                <td>{item.fragile ? "⚠️" : ""}</td>
                <td>{item.status}</td>
                <td>{item.top_missing_fact ? `Ask: ${item.top_missing_fact.why}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
