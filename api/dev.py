"""Developer-only routes for the /tests page: run the repo's own pytest suite and report each test.

Runs a fixed command (the tests/ directory, nothing taken from the request) in a subprocess. On by default for
local demo machines; set LAPSE_DEV=0 to turn it off anywhere else.
"""
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
def run_tests() -> dict:
    _enabled()
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        env = {**os.environ, "LAPSE_OFFLINE": "1"}          # the suite must never spend money or need a network
        t0 = time.time()
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests",
                               f"--junitxml={junit}"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        seconds = round(time.time() - t0, 1)
        if not junit.exists():
            raise HTTPException(status_code=500, detail=proc.stdout[-2000:] + proc.stderr[-2000:])
        tests = _parse(junit)
    counts = {k: sum(t["outcome"] == k for t in tests) for k in ("passed", "failed", "error", "skipped")}
    return {"ok": proc.returncode == 0, "seconds": seconds, **counts, "tests": tests,
            "summary": (proc.stdout.strip().splitlines() or [""])[-1]}
