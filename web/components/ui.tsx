"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { Bucket, Source } from "@/lib/types";
import { BUCKET_LABEL, SOURCE_LABEL } from "./labels";
import u from "./ui.module.css";

export { u };

function LogoMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true">
      <path d="M3 17 10 3l7 14z" fill="#e0643a" />
      <path d="M7.5 17 10 12l2.5 5z" fill="#1e1e1e" />
    </svg>
  );
}

const LINKS = [
  { href: "/", label: "Queue" },
  { href: "/plan", label: "Plan" },
  { href: "/eval", label: "Eval" },
  { href: "/tests", label: "Tests" },
];

export function Nav({ right }: { right?: React.ReactNode }) {
  const path = usePathname();
  // Hidden demo shortcut: Alt+Shift+R restores the golden cases, same as `make reset`.
  useEffect(() => {
    const onKey = async (e: KeyboardEvent) => {
      if (e.altKey && e.shiftKey && e.code === "KeyR") {
        e.preventDefault();
        await fetch("/api/demo/reset", { method: "POST" });
        window.location.reload();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const active = (href: string) => (href === "/" ? path === "/" || path.startsWith("/cases") : path.startsWith(href));
  return (
    <header className={u.wrap}>
      <nav className={u.nav} aria-label="Main">
        <div className={u.brand}>
          <Link href="/" className={u.wordmark}><LogoMark />Lapse</Link>
          <div className={u.links}>
            {LINKS.map((l) => (
              <Link key={l.href} href={l.href} aria-current={active(l.href) ? "page" : undefined}>{l.label}</Link>
            ))}
          </div>
        </div>
        <div className={u.navRight}>
          <span className={u.synthetic}>Synthetic data</span>
          {right}
        </div>
      </nav>
    </header>
  );
}

export function Footer() {
  return (
    <footer className={`${u.footer} ${u.wrap}`}>
      <span>Synthetic data only. No PHI.</span>
      <span>Lapse keeps eligible people covered.</span>
    </footer>
  );
}

const BUCKET_ICON: Record<Bucket, JSX.Element> = {
  SAFE: <path d="M5 10.5 8.5 14 15 6.5" />,
  PROVABLE: <path d="M4 16l2-5L13 4l3 3-7 7zM11.5 5.5l3 3" />,
  ONE_AWAY: <><circle cx="10" cy="10" r="7" /><path d="M9 7l1.5-1v8" /></>,
  NO_PATH: <path d="M5 10h10" />,
};

export function BucketTag({ bucket, big, flip }: { bucket: Bucket; big?: boolean; flip?: boolean }) {
  return (
    <span className={`${u.tag} ${u[`tag-${bucket}`]} ${big ? u.tagBig : ""} ${flip ? u.flip : ""}`}>
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{BUCKET_ICON[bucket]}</svg>
      {BUCKET_LABEL[bucket]}
    </span>
  );
}

const SOURCE_ICON: Record<Source, JSX.Element> = {
  billing_code: <path d="M6 3h8v14l-2-1.5-2 1.5-2-1.5L6 17zM8.5 7h3M8.5 10h3" />,
  structured_record: <path d="M3.5 5.5h13v9h-13zM6.5 9a1.5 1.5 0 1 0 0 .1M10 8.5h4M10 11.5h3" />,
  note_span: <path d="M6 3h6l3 3v11H6zM8.5 9h4M8.5 12h4" />,
  patient_reply: <path d="M4 5h12v8H9l-4 3v-3H4z" />,
  clinician_attestation: <path d="M3.5 15c2-3 3.5-3 4.5-1s2.5 2 4-1 2.5-3 4.5-2M11 4l3 3-5 5H6V9z" />,
  external_db: <path d="M4 5c0-1.6 12-1.6 12 0v10c0 1.6-12 1.6-12 0zM4 5c0 1.6 12 1.6 12 0M4 10c0 1.6 12 1.6 12 0" />,
};

/** The provenance signature: one mark per source type, everywhere a fact appears. */
export function SourceMark({ source, label }: { source: Source; label?: boolean }) {
  return (
    <span className={u.markRow}>
      <span className={`${u.mark} ${u[`src-${source}`]}`} title={SOURCE_LABEL[source]}>
        <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="#1e1e1e" strokeWidth="1.7"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{SOURCE_ICON[source]}</svg>
      </span>
      {label ? <span>{SOURCE_LABEL[source]}</span> : <span className={u.visuallyHidden}>{SOURCE_LABEL[source]}</span>}
    </span>
  );
}

/** Counts from 0 to value once, on first render with a value (the reveal). Honors reduced motion. */
export function AnimatedNumber({ value, ms = 1100 }: { value: number | null; ms?: number }) {
  const [shown, setShown] = useState<number | null>(null);
  const started = useRef(false);
  useEffect(() => {
    if (value === null) {
      setShown(null);
      started.current = false;
      return;
    }
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || started.current) {
      setShown(value);
      return;
    }
    started.current = true;
    const t0 = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / ms);
      setShown(Math.round(value * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, ms]);
  return <>{shown === null ? "–" : shown.toLocaleString()}</>;
}
