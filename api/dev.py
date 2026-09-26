"""Developer-only routes for the Verification page: run the repo's own pytest suite and report each test.

    POST /api/dev/tests              the full suite, offline: model responses are replayed from the recorded cache
    POST /api/dev/tests?mode=live    the live model tests only: real calls to the configured model (costs money)

Runs a fixed command (nothing taken from the request but the mode) in a subprocess. On by default for local demo
machines; set LAPSE_DEV=0 to turn it off anywhere else.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi import APIRouter, HTTPException

ROOT = Path(__file__).resolve().parent.parent
router = APIRouter(prefix="/api/dev")


def _enabled() -> None:
    if os.environ.get("LAPSE_DEV", "1") == "0":
        raise HTTPException(status_code=404, detail="developer routes are disabled")


def _parse(junit: Path) -> list[dict]:
    tests = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            node = case.find(tag)
            if node is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = (node.get("message") or node.text or "").strip()[:600]
                break
        tests.append({"file": case.get("classname", "").removeprefix("tests.").replace(".", "/") + ".py",
                      "name": case.get("name"), "outcome": outcome, "seconds": float(case.get("time", 0)),
                      "message": message})
    return tests


@router.post("/tests")
def run_tests(mode: str = "offline") -> dict:
    _enabled()
    if mode not in ("offline", "live"):
        raise HTTPException(status_code=400, detail="mode must be offline or live")
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        report = Path(tmp) / "live.json"
        if mode == "offline":
            env = {**os.environ, "LAPSE_OFFLINE": "1"}      # never spends money, never needs a network
            target, timeout = "tests", 300
        else:
            env = {k: v for k, v in os.environ.items() if k != "LAPSE_OFFLINE"}
            env.update({"LAPSE_LIVE": "1", "LAPSE_LIVE_REPORT": str(report)})
            target, timeout = "tests/test_live_llm.py", 600
        t0 = time.time()
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target,
                               f"--junitxml={junit}"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        seconds = round(time.time() - t0, 1)
        if not junit.exists():
            raise HTTPException(status_code=500, detail=proc.stdout[-2000:] + proc.stderr[-2000:])
        tests = _parse(junit)
        live = json.loads(report.read_text()) if report.exists() else None
    if mode == "offline":        # the live tests always skip offline; leave them out rather than show them as skipped
        tests = [t for t in tests if t["file"] != "test_live_llm.py"]
    counts = {k: sum(t["outcome"] == k for t in tests) for k in ("passed", "failed", "error", "skipped")}
    return {"ok": proc.returncode == 0, "mode": mode, "seconds": seconds, **counts, "tests": tests, "live": live,
            "summary": (proc.stdout.strip().splitlines() or [""])[-1]}
