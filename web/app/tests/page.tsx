"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { SCENARIOS, resetDemo, runEngineSuite, type Check, type EngineRun, type Scenario } from "@/lib/scenarios";
import s from "./tests.module.css";

type Status = "idle" | "running" | "pass" | "fail";

interface Result {
  status: Status;
  checks: Check[];
  ms?: number;
  error?: string;
}

const IDLE: Result = { status: "idle", checks: [] };

const ICONS: Record<string, JSX.Element> = {
  reset: <path d="M4 10a6 6 0 1 0 2-4.5M4 3v3.5h3.5" />,
  numbers: <path d="M4 16V9m5 7V5m5 11v-5" />,
  queue: <path d="M4 5h12M4 10h12M4 15h8" />,
  "rosa-record": <path d="M6 3h6l3 3v11H6zM8 9h5M8 12h5" />,
  "rosa-loop": <path d="M4 5h12v8H9l-4 3v-3H4z" />,
  deshawn: <path d="M4 5c0-1.5 12-1.5 12 0v10c0 1.5-12 1.5-12 0zM4 5c0 1.5 12 1.5 12 0M4 10c0 1.5 12 1.5 12 0" />,
  vague: <path d="M7.5 7.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .8-1 1.5M10 14.5v.5" />,
  karen: <path d="M10 3 17 16H3zM10 8v4M10 14v.5" />,
  bea: <path d="M5 5l10 10M15 5 5 15" />,
  eval: <path d="M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />,
  copy: <path d="M5 7h3v4H5zm0 4c0 2-1 3-2 3M12 7h3v4h-3zm0 4c0 2-1 3-2 3" />,
};

function Icon({ id }: { id: string }) {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="#1e1e1e" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[id]}
    </svg>
  );
}

function Mark() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true">
      <path d="M3 17 10 3l7 14z" fill="#e0643a" />
      <path d="M7.5 17 10 12l2.5 5z" fill="#1e1e1e" />
    </svg>
  );
}

const STATUS_TEXT: Record<Status, string> = { idle: "Not run", running: "Running", pass: "Passed", fail: "Failed" };

export default function TestsPage() {
  const [results, setResults] = useState<Record<string, Result>>({});
  const [busy, setBusy] = useState(false);
  const [engine, setEngine] = useState<EngineRun | null>(null);
  const [engineBusy, setEngineBusy] = useState(false);
  const [engineError, setEngineError] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const set = (id: string, r: Result) => setResults((prev) => ({ ...prev, [id]: r }));

  async function runOne(sc: Scenario) {
    set(sc.id, { status: "running", checks: [] });
    const t0 = performance.now();
    try {
      const checks = await sc.run();
      set(sc.id, { status: checks.every((c) => c.ok) ? "pass" : "fail", checks, ms: performance.now() - t0 });
    } catch (e) {
      set(sc.id, { status: "fail", checks: [], ms: performance.now() - t0, error: String(e) });
    }
  }

  async function runSingle(sc: Scenario) {
    setBusy(true);
    await runOne(sc);
    setBusy(false);
  }

  async function runAll() {
    setBusy(true);
    setResults({});
    for (const sc of SCENARIOS) await runOne(sc);
    await resetDemo();                       // leave the demo ready for the next rehearsal
    setBusy(false);
  }

  async function runEngine() {
    setEngineBusy(true);
    setEngineError(null);
    try {
      const run = await runEngineSuite();
      setEngine(run);
      setOpen(Object.fromEntries(run.tests.filter((t) => t.outcome === "failed" || t.outcome === "error")
        .map((t) => [t.file, true])));
    } catch (e) {
      setEngineError(String(e));
    } finally {
      setEngineBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    await resetDemo();
    setResults({});
    setBusy(false);
  }

  const done = SCENARIOS.filter((sc) => ["pass", "fail"].includes(results[sc.id]?.status ?? ""));
  const passed = done.filter((sc) => results[sc.id].status === "pass").length;
  const failed = done.length - passed;
  const checksRun = done.reduce((n, sc) => n + results[sc.id].checks.length, 0);

  const files = useMemo(() => {
    const by: Record<string, EngineRun["tests"]> = {};
    for (const t of engine?.tests ?? []) (by[t.file] ??= []).push(t);
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [engine]);

  return (
    <div className={s.page}>
      <header className={s.wrap}>
        <nav className={s.nav} aria-label="Main">
          <div className={s.brand}>
            <Link href="/" className={s.wordmark}><Mark />Lapse</Link>
            <div className={s.links}>
              <Link href="/">Queue</Link>
              <Link href="/plan">Plan</Link>
              <Link href="/eval">Eval</Link>
              <Link href="/tests" aria-current="page">Tests</Link>
            </div>
          </div>
          <div className={s.actions}>
            <button className={s.btn} onClick={reset} disabled={busy}>Reset demo</button>
            <button className={s.btnDark} onClick={runAll} disabled={busy}>Run all →</button>
          </div>
        </nav>
      </header>

      <main>
        <section className={s.hero}>
          <div className={s.notes} aria-hidden="true">
            <div className={`${s.scribble} ${s.scribbleL}`}>✓ no PHI<br />ever</div>
            <div className={`${s.scribble} ${s.scribbleR}`}>replays<br />offline ↺</div>
            <div className={`${s.note} ${s.noteA}`}>
              <span className={s.noteLabel}>Stories</span>
              <span className={s.noteValue}>{SCENARIOS.length}</span>
            </div>
            <div className={`${s.note} ${s.noteB}`}>
              <span className={s.noteLabel}>Passed</span>
              <span className={s.noteValue}>{done.length ? `${passed}/${done.length}` : "–"}</span>
            </div>
            <div className={`${s.note} ${s.noteC}`}>
              <span className={s.noteLabel}>Failed</span>
              <span className={s.noteValue}>{done.length ? failed : "–"}</span>
            </div>
            <div className={`${s.note} ${s.noteD}`}>
              <span className={s.noteLabel}>Engine</span>
              <span className={s.noteValue}>{engine ? engine.passed : "–"}</span>
            </div>
          </div>
          <p className={s.eyebrow}>Demo QA on <span className={s.badge}>synthetic data</span></p>
          <h1 className={s.h1}>Test the whole loop.</h1>
          <p className={s.lede}>
            Every story the demo tells, run against the live API. Rosa's reply, Deshawn's lookup, the
            numbers on the big screen.
          </p>
          <div className={s.heroButtons}>
            <button className={s.btn} onClick={runEngine} disabled={engineBusy}>Run engine suite</button>
            <button className={s.btnDark} onClick={runAll} disabled={busy}>Run all stories →</button>
          </div>
        </section>

        <section className={`${s.section} ${s.wrap}`} aria-labelledby="stories">
          <div className={s.sectionHead}>
            <div>
              <p className={s.eyebrow}>The stories</p>
              <h2 id="stories" className={s.h2}>What has to be true before the demo.</h2>
            </div>
            <p className={s.eyebrow} aria-live="polite">
              {busy ? "Running…" : done.length ? `${passed} of ${done.length} passed · ${checksRun} checks` : "Nothing run yet"}
            </p>
          </div>
          <ol className={s.list}>
            {SCENARIOS.map((sc, i) => {
              const r = results[sc.id] ?? IDLE;
              return (
                <li key={sc.id} className={s.row}>
                  <div className={s.rowTop}>
                    <div className={s.chips}>
                      <span className={s.num}>{i + 1}.</span>
                      <span className={`${s.icon} ${s[`tone-${sc.tone}`]}`}><Icon id={sc.id} /></span>
                    </div>
                    <div>
                      <h3 className={s.rowTitle}>{sc.title}</h3>
                      <p className={s.rowBlurb}>{sc.blurb}</p>
                    </div>
                    <div className={s.rowRight}>
                      <span className={`${s.status} ${s[`st-${r.status}`]}`}>
                        <span className={s.dot} />{STATUS_TEXT[r.status]}
                      </span>
                      <button className={s.btnSmall} onClick={() => runSingle(sc)} disabled={busy}
                        aria-label={`Run: ${sc.title}`}>Run</button>
                    </div>
                  </div>
                  {r.checks.length > 0 && (
                    <ul className={s.checks}>
                      {r.checks.map((c, j) => (
                        <li key={j} className={s.check}>
                          <span className={`${s.mark} ${c.ok ? s.markOk : s.markBad}`} aria-label={c.ok ? "pass" : "fail"}>
                            {c.ok ? "✓" : "✕"}
                          </span>
                          <span>
                            {c.name}
                            {c.detail && <span className={s.detail}>{c.detail}</span>}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {r.error && <p className={s.error}>{r.error}</p>}
                  {r.ms !== undefined && (
                    <p className={s.meta}>
                      {r.checks.filter((c) => c.ok).length}/{r.checks.length} checks · {(r.ms / 1000).toFixed(2)}s
                      {sc.mutates ? " · resets the demo first" : ""}
                    </p>
                  )}
                </li>
              );
            })}
          </ol>
        </section>

        <section className={`${s.section} ${s.wrap}`} aria-labelledby="engine">
          <div className={s.sectionHead}>
            <div>
              <p className={s.eyebrow}>Under the hood</p>
              <h2 id="engine" className={s.h2}>The engine suite, offline.</h2>
            </div>
          </div>
          <div className={s.suite}>
            <div className={s.panel}>
              <p className={s.eyebrow} style={{ margin: 0 }}>Unit and integration tests</p>
              <p className={s.panelBig}>{engine ? `${engine.passed}/${engine.tests.length}` : "–"}</p>
              <p className={s.panelSub}>
                {engine ? `${engine.seconds}s · LLM calls replayed from cache` : "Runs pytest with LAPSE_OFFLINE=1"}
              </p>
              <button className={s.btnDark} onClick={runEngine} disabled={engineBusy}>
                {engineBusy ? "Running…" : engine ? "Run again →" : "Run the suite →"}
              </button>
              {engineError && <p className={s.error}>{engineError}</p>}
            </div>
            {files.length === 0 ? (
              <p className={s.empty}>Results by file appear here.</p>
            ) : (
              <ul className={s.files}>
                {files.map(([file, tests]) => {
                  const bad = tests.filter((t) => t.outcome === "failed" || t.outcome === "error").length;
                  const isOpen = !!open[file];
                  return (
                    <li key={file} className={s.file}>
                      <button className={s.fileHead} aria-expanded={isOpen}
                        onClick={() => setOpen((o) => ({ ...o, [file]: !o[file] }))}>
                        <span className={s.fileName}>{file}</span>
                        <span className={`${s.fileCount} ${bad ? s.markBad : ""}`}>
                          {bad ? `${bad} failed · ` : ""}{tests.length - bad}/{tests.length} passed {isOpen ? "▴" : "▾"}
                        </span>
                      </button>
                      {isOpen && (
                        <ul className={s.fileTests}>
                          {tests.map((t) => (
                            <li key={t.name} className={s.check}>
                              <span className={`${s.mark} ${t.outcome === "passed" ? s.markOk : s.markBad}`}>
                                {t.outcome === "passed" ? "✓" : t.outcome === "skipped" ? "–" : "✕"}
                              </span>
                              <span>
                                {t.name.replace(/^test_/, "").replaceAll("_", " ")}
                                {t.message && <span className={s.detail}>{t.message}</span>}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>

        <footer className={`${s.footer} ${s.wrap}`}>
          <span>Synthetic data only. No PHI.</span>
          <span>Lapse keeps eligible people covered.</span>
        </footer>
      </main>
    </div>
  );
}
