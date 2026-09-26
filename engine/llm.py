"""OpenAI wrapper with a disk cache. Every call is cached, so the demo replays offline after one warm run.

    call_json(model, system, user, schema)            -> dict      (one call)
    run_batch([{"model", "system", "user", "schema"}]) -> [dict]    (concurrent, same order)

Cache: .cache/llm/{sha256(model + system + user + schema)}.json. A hit makes no network call.
Set LAPSE_OFFLINE=1 to turn a cache miss into an error instead of a request (use it for demo runs).
Responses use OpenAI structured outputs, so the dict always matches the schema.
"""
import asyncio
import hashlib
import random
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv

from engine.config import LLM_CACHE_DIR, ROOT

load_dotenv(ROOT / ".env")

CONCURRENCY = 16
MAX_RETRIES = 2          # SDK-level; _with_backoff below does the patient retrying
BACKOFF_ATTEMPTS = 12
MAX_OUTPUT_TOKENS = 1500  # default cap; OpenAI charges prompt + max_tokens against the per-minute budget on admission

# Tokens per minute this account may use (x-ratelimit-limit-tokens). Batches pace themselves to TPM_HEADROOM of
# it so they rarely see a 429: bursting and backing off wasted ~75% of gpt-4.1's 30k budget on the first run.
TPM_LIMITS = {"gpt-4.1-mini": 200_000, "gpt-4.1": 30_000}
DEFAULT_TPM = 30_000
TPM_HEADROOM = 0.9


class CacheMiss(RuntimeError):
    pass


@dataclass
class Stats:
    hits: int = 0
    misses: int = 0
    input_tokens: dict[str, int] = field(default_factory=dict)
    output_tokens: dict[str, int] = field(default_factory=dict)

    def add(self, model: str, usage) -> None:
        self.misses += 1
        self.input_tokens[model] = self.input_tokens.get(model, 0) + usage.prompt_tokens
        self.output_tokens[model] = self.output_tokens.get(model, 0) + usage.completion_tokens


stats = Stats()


def _key(model: str, system: str, user: str, schema: dict) -> str:
    raw = json.dumps([model, system, user, schema], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def cached(model: str, system: str, user: str, schema: dict) -> dict | None:
    path = LLM_CACHE_DIR / f"{_key(model, system, user, schema)}.json"
    if path.exists():
        return json.loads(path.read_text())["response"]
    return None


def _store(model: str, system: str, user: str, schema: dict, response: dict) -> None:
    LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = LLM_CACHE_DIR / f"{_key(model, system, user, schema)}.json"
    body = json.dumps({"model": model, "system": system, "user": user, "response": response}, ensure_ascii=False)
    with tempfile.NamedTemporaryFile("w", dir=LLM_CACHE_DIR, delete=False, suffix=".tmp") as f:
        f.write(body)
    os.replace(f.name, path)     # atomic: a crash never leaves a half-written cache entry


def _request(model: str, system: str, user: str, schema: dict, max_tokens: int = MAX_OUTPUT_TOKENS) -> dict:
    return {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "result", "schema": schema, "strict": True}},
    }


def _offline() -> bool:
    return os.environ.get("LAPSE_OFFLINE") == "1"


def call_json(model: str, system: str, user: str, schema: dict, max_tokens: int = MAX_OUTPUT_TOKENS) -> dict:
    hit = cached(model, system, user, schema)
    if hit is not None:
        stats.hits += 1
        return hit
    if _offline():
        raise CacheMiss(f"LAPSE_OFFLINE=1 and no cached response for this {model} call")
    from openai import OpenAI

    resp = OpenAI(max_retries=MAX_RETRIES).chat.completions.create(**_request(model, system, user, schema, max_tokens))
    out = json.loads(resp.choices[0].message.content)
    stats.add(model, resp.usage)
    _store(model, system, user, schema, out)
    return out


class TokenBucket:
    """Paces requests to a tokens-per-minute budget. acquire(n) waits until n tokens have refilled."""

    def __init__(self, per_minute: float, clock=time.monotonic):
        self.capacity = per_minute
        self.rate = per_minute / 60
        self.tokens = per_minute
        self.clock = clock
        self.last = clock()
        self.lock = asyncio.Lock()

    async def acquire(self, n: float, sleep=asyncio.sleep) -> None:
        n = min(n, self.capacity)
        async with self.lock:          # FIFO: waiters queue up instead of racing each other into 429s
            while True:
                now = self.clock()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                await sleep((n - self.tokens) / self.rate)


def estimate_tokens(c: dict) -> int:
    """What OpenAI reserves on admission: prompt (chars/3, deliberately high) + the output cap."""
    prompt = len(c["system"]) + len(c["user"]) + len(json.dumps(c["schema"]))
    return prompt // 3 + c.get("max_tokens", MAX_OUTPUT_TOKENS)


def retry_after(err: Exception, attempt: int) -> float:
    """How long OpenAI asked us to wait ("try again in 259ms"), else a capped exponential guess."""
    m = re.search(r"try again in ([\d.]+)(ms|s)", str(err))
    if m:
        wait = float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
        return wait + 0.05 + 0.25 * random.random()
    return min(20, 2 ** attempt) * (0.75 + 0.5 * random.random())


async def _with_backoff(make_call):
    import openai

    for attempt in range(BACKOFF_ATTEMPTS):
        try:
            return await make_call()
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
            if attempt == BACKOFF_ATTEMPTS - 1:
                raise
            await asyncio.sleep(retry_after(e, attempt))


async def _batch(calls: list[dict], concurrency: int) -> list[dict]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(max_retries=MAX_RETRIES)
    sem = asyncio.Semaphore(concurrency)
    buckets = {m: TokenBucket(TPM_LIMITS.get(m, DEFAULT_TPM) * TPM_HEADROOM) for m in {c["model"] for c in calls}}

    async def one(c: dict) -> dict:
        hit = cached(c["model"], c["system"], c["user"], c["schema"])
        if hit is not None:
            stats.hits += 1
            return hit
        if _offline():
            raise CacheMiss(f"LAPSE_OFFLINE=1 and no cached response for this {c['model']} call")
        async with sem:
            await buckets[c["model"]].acquire(estimate_tokens(c))
            resp = await _with_backoff(lambda: client.chat.completions.create(
                **_request(c["model"], c["system"], c["user"], c["schema"], c.get("max_tokens", MAX_OUTPUT_TOKENS))))
        out = json.loads(resp.choices[0].message.content)
        stats.add(c["model"], resp.usage)
        _store(c["model"], c["system"], c["user"], c["schema"], out)
        return out

    try:
        return await asyncio.gather(*(one(c) for c in calls))
    finally:
        await client.close()


def run_batch(calls: list[dict], concurrency: int = CONCURRENCY) -> list[dict]:
    """Run many calls concurrently; results come back in input order. Cached calls cost nothing."""
    return asyncio.run(_batch(calls, concurrency))


def uncached(calls: list[dict]) -> list[dict]:
    return [c for c in calls if cached(c["model"], c["system"], c["user"], c["schema"]) is None]
