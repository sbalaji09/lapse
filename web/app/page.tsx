"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

export default function QueuePage() {
  const [health, setHealth] = useState<string>("loading...");

  useEffect(() => {
    apiFetch("/api/health")
      .then((res) => res.json())
      .then((data) => setHealth(JSON.stringify(data)))
      .catch((err) => setHealth(`error: ${err}`));
  }, []);

  return (
    <div>
      <h1>Queue</h1>
      <p>{health}</p>
    </div>
  );
}
