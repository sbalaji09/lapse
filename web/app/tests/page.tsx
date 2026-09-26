"use client";

import { useMemo, useState } from "react";
import {
  GROUPS, SCENARIOS, SUITE_AREAS, plain, restoreSampleData, runEngineSuite, type Check, type EngineRun, type Scenario,
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

type Mode = "offline" | "live";
type Areas = Array<[string, EngineRun["tests"]]>;

function byArea(run: EngineRun | null): Areas {
  const by: Record<string, EngineRun["tests"]> = {};
  for (const t of run?.tests ?? []) (by[t.file] ??= []).push(t);
  return Object.entries(by).sort(([a], [b]) => (SUITE_AREAS[a] ?? a).localeCompare(SUITE_AREAS[b] ?? b));
}

const MEMBER_EXPECTED: Record<string, { name: string; expected: string }> = {
  "g-rosa": { name: "Rosa Delgado", expected: "not_determined" },
  "g-marcus": { name: "Marcus Webb", expected: "exempt" },
  "g-deshawn": { name: "Deshawn Price", expected: "not_determined" },
  "g-linh": { name: "Linh Tran", expected: "exempt" },
  "g-karen": { name: "Karen Hollis", expected: "exempt" },
  "g-omar": { name: "Omar Haddad", expected: "compliant" },
  "g-bea": { name: "Bea Knox", expected: "not_determined" },
};

function LiveSummary({ run }: { run: EngineRun }) {
  const r = run.live!;
  const n = r.sample_notes;
  return (
    <div className={s.live}>
      <div className={s.liveFacts}>
        <div><span>Model provider</span>{r.provider === "bedrock" ? "AWS Bedrock" : "OpenAI"}</div>
        <div><span>Evidence finder</span>{r.models.extraction}</div>
        <div><span>Verifier</span>{r.models.verifier}</div>
        <div><span>Live calls made</span>{r.calls} (none replayed)</div>
        <div><span>Cost</span>${r.cost_usd.toFixed(2)}</div>
        <div><span>Time</span>{r.seconds} s</div>
      </div>
      <div className={s.liveCols}>
        {r.sample_members && (
          <table className={s.results}>
            <thead><tr><th scope="col">Sample member</th><th scope="col">Expected</th><th scope="col">Observed</th><th scope="col">Result</th></tr></thead>
            <tbody>
              {Object.entries(MEMBER_EXPECTED).map(([id, m]) => {
                const got = r.sample_members![id];
                const ok = got === m.expected;
                return (
                  <tr key={id}>
                    <td>{m.name}</td><td>{plain(m.expected)}</td><td className={s.observed}>{plain(got)}</td>
                    <td className={`${s.resultCell} ${ok ? s.ok : s.bad}`}>{ok ? "✓ Pass" : "✕ Fail"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {n && (
          <table className={s.results}>
            <thead><tr><th scope="col">On {n.notes} generated notes</th><th scope="col">Required</th><th scope="col">Observed</th></tr></thead>
            <tbody>
              <tr><td>Evidence found that matches a label (precision)</td><td>0.85 or more</td><td className={s.observed}>{n.precision.toFixed(2)}</td></tr>
              <tr><td>Labeled evidence that was found (recall)</td><td>0.75 or more</td><td className={s.observed}>{n.recall.toFixed(2)}</td></tr>
              <tr><td>Negated, past or family sentences counted as evidence</td><td>None</td><td className={s.observed}>{n.distractors_counted} of {n.distractors}</td></tr>
              <tr><td>Claims rejected by the verifier</td><td>Any</td><td className={s.observed}>{n.dropped_by_verifier}</td></tr>
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Areas({ mode, areas, open, setOpen }: {
  mode: Mode; areas: Areas; open: Record<string, boolean>;
  setOpen: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
}) {
  return (
    <ul className={s.areas}>
      {areas.map(([file, tests]) => {
        const bad = tests.filter((t) => t.outcome === "failed" || t.outcome === "error").length;
        const key = `${mode}:${file}`;
        const isOpen = !!open[key];
        return (
          <li key={key} className={s.area}>
            <button className={s.areaHead} aria-expanded={isOpen} onClick={() => setOpen((o) => ({ ...o, [key]: !o[key] }))}>
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
  );
}

export default function VerificationPage() {
  const [results, setResults] = useState<Record<string, Result>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<Mode, EngineRun | null>>({ offline: null, live: null });
  const [running, setRunning] = useState<Mode | null>(null);
  const [engineError, setEngineError] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const engine = runs.offline;

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

  async function runEngine(mode: Mode) {
    setRunning(mode);
    setEngineError(null);
    try {
      const run = await runEngineSuite(mode);
      setRuns((r) => ({ ...r, [mode]: run }));
      setOpen((o) => ({ ...o, ...Object.fromEntries(run.tests.filter((t) => t.outcome === "failed" || t.outcome === "error")
        .map((t) => [`${mode}:${t.file}`, true])) }));
    } catch (e) {
      setEngineError(String(e).replace(/^Error: /, ""));
    } finally {
      setRunning(null);
    }
  }

  const done = SCENARIOS.filter((sc) => ["pass", "fail"].includes(results[sc.id]?.status ?? ""));
  const passed = done.filter((sc) => results[sc.id].status === "pass").length;
  const checks = done.flatMap((sc) => results[sc.id].checks);
  const checksOk = checks.filter((c) => c.ok).length;
  const lastAt = done.map((sc) => results[sc.id].at).filter(Boolean).sort().pop();

  const areas = useMemo(() => byArea(engine), [engine]);

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
          <div className={s.headSide}>
            <div className={s.headActions}>
              <button className={u.btn} onClick={restore} disabled={busy}>Restore sample data</button>
              <button className={u.btnDark} onClick={runAll} disabled={busy}>
                {busy ? "Running checks…" : "Run all checks →"}
              </button>
            </div>
            <p className={s.headHelp}>
              <strong>Run all checks</strong> runs the ten scenarios below against the running system, one after
              another, about ten seconds in all. <strong>Restore sample data</strong> puts the seven sample members
              back as they were; running the checks does this automatically at the end.
            </p>
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
            <div className={s.tileLabel}>Automated tests (recorded)</div>
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
            <h2 id="engine" className={s.groupTitle}>Automated tests</h2>
            <p className={s.groupIntro}>The test suite behind the product, in two forms.</p>
          </div>
          <div className={s.suiteModes}>
            <div className={`${s.panel} ${s.panelOffline}`}>
              <div className={s.tileLabel}>Recorded responses · free · about 5 seconds</div>
              <h3 className={s.panelTitle}>Full test suite</h3>
              <p className={s.panelText}>
                Every automated test of the product: the simulated state check, the evidence finder and verifier, the
                missing-fact solver, the member and clinician workflow, and the accuracy measurement. Language model
                answers are replayed from a recording made during the last full run, so it is fast, free, needs no
                network, and gives the same result every time.
              </p>
              <p className={s.panelBig}>{engine ? `${engine.passed} / ${engine.tests.length}` : "Not run"}</p>
              <button className={u.btnDark} onClick={() => runEngine("offline")} disabled={running !== null}>
                {running === "offline" ? "Running tests…" : engine ? "Run again →" : "Run the full suite →"}
              </button>
            </div>
            <div className={`${s.panel} ${s.panelLive}`}>
              <div className={s.tileLabel}>Live language model · about $0.10 · under a minute</div>
              <h3 className={s.panelTitle}>Live model tests</h3>
              <p className={s.panelText}>
                Calls the real language model with nothing replayed, to confirm it still behaves: the seven sample
                members must come out as expected, and on 30 generated notes the evidence it finds must match the
                labels we wrote. The recorded responses used everywhere else are not changed.
              </p>
              <p className={s.panelBig}>{runs.live ? `${runs.live.passed} / ${runs.live.tests.length}` : "Not run"}</p>
              <button className={u.btnDark} onClick={() => runEngine("live")} disabled={running !== null}>
                {running === "live" ? "Calling the model…" : runs.live ? "Run again →" : "Run live model tests →"}
              </button>
            </div>
          </div>
          {engineError && <p className={s.error}>{engineError}</p>}

          {runs.live?.live && <LiveSummary run={runs.live} />}
          {runs.live && <Areas mode="live" areas={byArea(runs.live)} open={open} setOpen={setOpen} />}
          {engine && (
            <>
              <h3 className={s.subhead}>Full suite results</h3>
              <Areas mode="offline" areas={areas} open={open} setOpen={setOpen} />
            </>
          )}
        </section>
      </main>
      <Footer />
    </>
  );
}
