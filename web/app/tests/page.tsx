"use client";

import { useMemo, useState } from "react";
import {
  GROUPS, SCENARIOS, SUITE_AREAS, restoreSampleData, runEngineSuite, type Check, type EngineRun, type Scenario,
} from "@/lib/scenarios";
import { Footer, Nav, u } from "@/components/ui";
import s from "./tests.module.css";

type Status = "idle" | "running" | "pass" | "fail";

interface Result {
  status: Status;
  checks: Check[];
  ms?: number;
  error?: string;
  at?: Date;
}

const IDLE: Result = { status: "idle", checks: [] };
const BADGE: Record<Status, string> = { idle: "Not yet run", running: "Running", pass: "Verified", fail: "Failed" };

function humanTestName(name: string): string {
  const t = name.replace(/^test_/, "").replaceAll("_", " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export default function VerificationPage() {
  const [results, setResults] = useState<Record<string, Result>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
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
      set(sc.id, { status: checks.every((c) => c.ok) ? "pass" : "fail", checks, ms: performance.now() - t0, at: new Date() });
    } catch (e) {
      set(sc.id, { status: "fail", checks: [], ms: performance.now() - t0, error: String(e).replace(/^Error: /, "") });
    }
  }

  async function runSingle(sc: Scenario) {
    setBusy(true);
    setNote(null);
    await runOne(sc);
    setBusy(false);
  }

  async function runAll() {
    setBusy(true);
    setNote(null);
    setResults({});
    for (const sc of SCENARIOS) await runOne(sc);
    await restoreSampleData();          // leave the product exactly as it was found
    setBusy(false);
    setNote("All checks complete. The sample data has been restored to its original state.");
  }

  async function restore() {
    setBusy(true);
    const r = await restoreSampleData();
    setResults({});
    setBusy(false);
    setNote(`Sample data restored: ${r.reloaded} sample members returned to their original state.`);
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
      setEngineError(String(e).replace(/^Error: /, ""));
    } finally {
      setEngineBusy(false);
    }
  }

  const done = SCENARIOS.filter((sc) => ["pass", "fail"].includes(results[sc.id]?.status ?? ""));
  const passed = done.filter((sc) => results[sc.id].status === "pass").length;
  const checks = done.flatMap((sc) => results[sc.id].checks);
  const checksOk = checks.filter((c) => c.ok).length;
  const lastAt = done.map((sc) => results[sc.id].at).filter(Boolean).sort().pop();

  const areas = useMemo(() => {
    const by: Record<string, EngineRun["tests"]> = {};
    for (const t of engine?.tests ?? []) (by[t.file] ??= []).push(t);
    return Object.entries(by).sort(([a], [b]) => (SUITE_AREAS[a] ?? a).localeCompare(SUITE_AREAS[b] ?? b));
  }, [engine]);

  let n = 0;

  return (
    <>
      <Nav />
      <main className={u.wrap}>
        <section className={s.head}>
          <div>
            <p className={u.eyebrow}>Verification</p>
            <h1 className={u.h1}>Every claim, checked against the live system.</h1>
            <p className={s.lede}>
              Lapse tells a clinic which members the state&apos;s check will miss, why, and what single fact would keep
              each one covered. This page re-runs each of those claims against the running system and shows exactly
              what it found. All members shown are synthetic.
            </p>
          </div>
          <div className={s.headActions}>
            <button className={u.btn} onClick={restore} disabled={busy}>Restore sample data</button>
            <button className={u.btnDark} onClick={runAll} disabled={busy}>
              {busy ? "Running checks…" : "Run all checks →"}
            </button>
          </div>
        </section>

        <section className={s.tiles} aria-label="Summary">
          <div className={`${s.tile} ${done.length ? (passed === done.length ? s.tilePass : s.tileFail) : ""}`}>
            <div className={s.tileLabel}>Scenarios verified</div>
            <div className={s.tileValue}>{done.length ? `${passed} / ${SCENARIOS.length}` : `0 / ${SCENARIOS.length}`}</div>
            <div className={s.tileNote}>{done.length ? `${done.length - passed} failed` : "Not yet run"}</div>
          </div>
          <div className={`${s.tile} ${checks.length ? (checksOk === checks.length ? s.tilePass : s.tileFail) : ""}`}>
            <div className={s.tileLabel}>Individual checks passed</div>
            <div className={s.tileValue}>{checks.length ? `${checksOk} / ${checks.length}` : "None run"}</div>
            <div className={s.tileNote}>Each compares an expected result with what the system returned</div>
          </div>
          <div className={`${s.tile} ${engine ? (engine.ok ? s.tilePass : s.tileFail) : ""}`}>
            <div className={s.tileLabel}>Automated engine tests</div>
            <div className={s.tileValue}>{engine ? `${engine.passed} / ${engine.tests.length}` : "Not run"}</div>
            <div className={s.tileNote}>{engine ? `Completed in ${engine.seconds} seconds` : "Run from the section below"}</div>
          </div>
          <div className={s.tile}>
            <div className={s.tileLabel}>Last completed</div>
            <div className={s.tileValue}>
              {lastAt ? lastAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "Never"}
            </div>
            <div className={s.tileNote}>Results are not stored; every run is fresh</div>
          </div>
        </section>
        <p className={s.status} aria-live="polite">{note}</p>

        <section className={s.howto} aria-label="How to read this page">
          <div><strong>What it verifies</strong>The claim being tested, in plain terms.</div>
          <div><strong>Why it matters</strong>What would go wrong for a member or a clinic if it were false.</div>
          <div>
            <strong>Expected and observed</strong>
            Each check states the result the claim requires and the result the live system actually returned.
          </div>
        </section>

        {GROUPS.map((g) => (
          <section key={g.id} className={s.group} aria-labelledby={`g-${g.id}`}>
            <div className={s.groupHead}>
              <h2 id={`g-${g.id}`} className={s.groupTitle}>{g.title}</h2>
              <p className={s.groupIntro}>{g.intro}</p>
            </div>
            <ol className={s.list}>
              {SCENARIOS.filter((sc) => sc.group === g.id).map((sc) => {
                const r = results[sc.id] ?? IDLE;
                n += 1;
                return (
                  <li key={sc.id} className={`${s.card} ${r.status === "fail" ? s.cardFail : ""}`}>
                    <div className={s.cardTop}>
                      <span className={`${s.num} ${s[`tone-${sc.tone}`]}`} aria-hidden="true">{n}</span>
                      <div>
                        <h3 className={s.title}>{sc.title}</h3>
                        <dl className={s.explain}>
                          <dt>What it verifies</dt>
                          <dd>{sc.verifies}</dd>
                          <dt>Why it matters</dt>
                          <dd className={s.matters}>{sc.matters}</dd>
                        </dl>
                      </div>
                      <div className={s.right}>
                        <span className={`${s.badge} ${r.status === "pass" ? s.badgePass : r.status === "fail" ? s.badgeFail
                          : r.status === "running" ? s.badgeRunning : ""}`}>
                          {r.status === "pass" ? "✓ " : r.status === "fail" ? "✕ " : ""}{BADGE[r.status]}
                        </span>
                        <button className={u.btnQuiet} onClick={() => runSingle(sc)} disabled={busy}
                          aria-label={`Run this check: ${sc.title}`}>
                          Run this check
                        </button>
                      </div>
                    </div>

                    {r.checks.length > 0 && (
                      <table className={s.results}>
                        <thead>
                          <tr>
                            <th scope="col">Check</th>
                            <th scope="col">Expected</th>
                            <th scope="col">Observed</th>
                            <th scope="col">Result</th>
                          </tr>
                        </thead>
                        <tbody>
                          {r.checks.map((c, i) => (
                            <tr key={i}>
                              <td>{c.name}</td>
                              <td>{c.expected}</td>
                              <td className={s.observed}>{c.observed}</td>
                              <td className={`${s.resultCell} ${c.ok ? s.ok : s.bad}`}>{c.ok ? "✓ Pass" : "✕ Fail"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                    {r.error && <p className={s.error}>{r.error}</p>}
                    {r.ms !== undefined && (
                      <p className={s.meta}>
                        {r.checks.filter((c) => c.ok).length} of {r.checks.length} checks passed in {(r.ms / 1000).toFixed(2)} s
                        {sc.mutates ? ". Starts from the original sample data." : "."}
                      </p>
                    )}
                  </li>
                );
              })}
            </ol>
          </section>
        ))}

        <section className={s.group} aria-labelledby="engine">
          <div className={s.groupHead}>
            <h2 id="engine" className={s.groupTitle}>Automated engine tests</h2>
            <p className={s.groupIntro}>The test suite behind the product, run on demand.</p>
          </div>
          <div className={s.suite}>
            <div className={s.panel}>
              <div className={s.tileLabel}>Tests passed</div>
              <p className={s.panelBig}>{engine ? `${engine.passed} / ${engine.tests.length}` : "Not run"}</p>
              <p className={s.panelText}>
                Covers the simulated state check, the evidence finder and its verifier, the missing-fact solver, the
                member and clinician workflow, and the accuracy measurement. Language model responses are replayed
                from a recorded cache, so the suite runs without a network connection.
              </p>
              <button className={u.btnDark} onClick={runEngine} disabled={engineBusy}>
                {engineBusy ? "Running tests…" : engine ? "Run again →" : "Run the test suite →"}
              </button>
              {engineError && <p className={s.error}>{engineError}</p>}
            </div>
            {areas.length === 0 ? (
              <p className={s.empty}>Results appear here, grouped by the part of the product each test covers.</p>
            ) : (
              <ul className={s.areas}>
                {areas.map(([file, tests]) => {
                  const bad = tests.filter((t) => t.outcome === "failed" || t.outcome === "error").length;
                  const isOpen = !!open[file];
                  return (
                    <li key={file} className={s.area}>
                      <button className={s.areaHead} aria-expanded={isOpen}
                        onClick={() => setOpen((o) => ({ ...o, [file]: !o[file] }))}>
                        <span>
                          {SUITE_AREAS[file] ?? file}
                          <span className={s.areaFile}>{file}</span>
                        </span>
                        <span className={`${s.areaCount} ${bad ? s.bad : ""}`}>
                          {bad ? `${bad} failed · ` : ""}{tests.length - bad} of {tests.length} passed {isOpen ? "▴" : "▾"}
                        </span>
                      </button>
                      {isOpen && (
                        <ul className={s.areaTests}>
                          {tests.map((t) => (
                            <li key={t.name}>
                              <span className={t.outcome === "passed" ? s.ok : s.bad}>
                                {t.outcome === "passed" ? "✓" : t.outcome === "skipped" ? "–" : "✕"}
                              </span>
                              <span>
                                {humanTestName(t.name)}
                                {t.message && <span className={s.testMsg}>{t.message}</span>}
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
      </main>
      <Footer />
    </>
  );
}
