"""LLM provider wrapper with a disk cache. Every call is cached, so the demo replays offline after one warm run.

    call_json(model, system, user, schema)            -> dict      (one call)
    run_batch([{"model", "system", "user", "schema"}]) -> [dict]    (concurrent, same order)

Set LAPSE_BACKEND=aws for Bedrock; local uses OpenAI as the explicit fallback.
Cache: .cache/llm/{sha256(provider model + system + user + schema)}.json. A hit makes no network call.
Set LAPSE_OFFLINE=1 to turn a cache miss into an error instead of a request (use it for demo runs).
Set LAPSE_LLM_CACHE_DIR to use a different cache folder: an empty one forces every call to the live model
without touching the recorded demo cache (the live model tests do this).
Responses use provider-native structured outputs.
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
from pathlib import Path

from engine import config
from engine.config import LLM_CACHE_DIR

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
        if isinstance(usage, dict):
            input_tokens = usage.get("inputTokens", 0)
            output_tokens = usage.get("outputTokens", 0)
        else:
            input_tokens = usage.prompt_tokens
            output_tokens = usage.completion_tokens
        self.misses += 1
        self.input_tokens[model] = self.input_tokens.get(model, 0) + input_tokens
        self.output_tokens[model] = self.output_tokens.get(model, 0) + output_tokens


stats = Stats()


def _provider() -> str:
    return "bedrock" if os.environ.get("LAPSE_BACKEND", config.LAPSE_BACKEND) == "aws" else "openai"


def _effective_model(model: str) -> str:
    """Map the engine's fast/verifier roles to their configured Bedrock model IDs."""
    if _provider() != "bedrock":
        return model

    fast = os.environ.get("BEDROCK_MODEL_FAST", config.BEDROCK_MODEL_FAST)
    verify = os.environ.get("BEDROCK_MODEL_VERIFY", config.BEDROCK_MODEL_VERIFY)
    if model in {config.OPENAI_MODEL_FAST, config.BEDROCK_MODEL_FAST, config.MODEL_FAST, fast}:
        return fast
    if model in {config.OPENAI_MODEL_VERIFY, config.BEDROCK_MODEL_VERIFY, config.MODEL_VERIFY, verify}:
        return verify
    return model


def _key(model: str, system: str, user: str, schema: dict) -> str:
    # Keep legacy OpenAI keys stable while isolating Bedrock caches by the exact deployed model ID.
    cache_model = model if _provider() == "openai" else f"bedrock:{_effective_model(model)}"
    raw = json.dumps([cache_model, system, user, schema], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_dir() -> Path:
    override = os.environ.get("LAPSE_LLM_CACHE_DIR")
    return Path(override) if override else LLM_CACHE_DIR


def cached(model: str, system: str, user: str, schema: dict) -> dict | None:
    path = _cache_dir() / f"{_key(model, system, user, schema)}.json"
    if path.exists():
        return json.loads(path.read_text())["response"]
    return None


def _store(model: str, system: str, user: str, schema: dict, response: dict) -> None:
    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_key(model, system, user, schema)}.json"
    body = json.dumps({
        "provider": _provider(),
        "model": _effective_model(model),
        "system": system,
        "user": user,
        "response": response,
    }, ensure_ascii=False)
    with tempfile.NamedTemporaryFile("w", dir=cache_dir, delete=False, suffix=".tmp") as f:
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


def _bedrock_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", config.AWS_REGION),
        config=Config(retries={"max_attempts": MAX_RETRIES, "mode": "adaptive"}),
    )


def _bedrock_request(
    model: str,
    system: str,
    user: str,
    schema: dict,
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> dict:
    return {
        "modelId": _effective_model(model),
        "system": [{"text": system}],
        "messages": [{"role": "user", "content": [{"text": user}]}],
        "inferenceConfig": {"temperature": 0, "maxTokens": max_tokens},
        "toolConfig": {
            "tools": [{
                "toolSpec": {
                    "name": "return_json",
                    "description": "Return JSON matching the required schema.",
                    "inputSchema": {"json": schema},
                }
            }],
            "toolChoice": {"tool": {"name": "return_json"}},
        },
    }


def _bedrock_response(response: dict) -> dict:
    content = response["output"]["message"]["content"]
    for block in content:
        tool_use = block.get("toolUse")
        if tool_use and tool_use.get("name") == "return_json":
            result = tool_use.get("input")
            if isinstance(result, dict):
                return result

    # Defensive fallback for a model that emits valid JSON text despite the forced tool choice.
    text = "".join(block.get("text", "") for block in content)
    if text:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    raise ValueError("Bedrock response did not contain a return_json tool result")


def _offline() -> bool:
    return os.environ.get("LAPSE_OFFLINE") == "1"


def call_json(model: str, system: str, user: str, schema: dict, max_tokens: int = MAX_OUTPUT_TOKENS) -> dict:
    hit = cached(model, system, user, schema)
    if hit is not None:
        stats.hits += 1
        return hit
    if _offline():
        raise CacheMiss(f"LAPSE_OFFLINE=1 and no cached response for this {model} call")

    if _provider() == "bedrock":
        effective_model = _effective_model(model)
        resp = _bedrock_client().converse(**_bedrock_request(model, system, user, schema, max_tokens))
        out = _bedrock_response(resp)
        stats.add(effective_model, resp.get("usage", {}))
    else:
        from openai import OpenAI

        resp = OpenAI(max_retries=MAX_RETRIES).chat.completions.create(
            **_request(model, system, user, schema, max_tokens)
        )
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
    results = [None] * len(calls)
    pending = []
    for index, call in enumerate(calls):
        hit = cached(call["model"], call["system"], call["user"], call["schema"])
        if hit is not None:
            stats.hits += 1
            results[index] = hit
        else:
            pending.append((index, call))

    if not pending:
        return results
    if _offline():
        model = pending[0][1]["model"]
        raise CacheMiss(f"LAPSE_OFFLINE=1 and no cached response for this {model} call")

    sem = asyncio.Semaphore(concurrency)

    if _provider() == "bedrock":
        client = _bedrock_client()

        async def one_bedrock(index: int, call: dict) -> None:
            async with sem:
                response = await asyncio.to_thread(
                    client.converse,
                    **_bedrock_request(
                        call["model"],
                        call["system"],
                        call["user"],
                        call["schema"],
                        call.get("max_tokens", MAX_OUTPUT_TOKENS),
                    ),
                )
            result = _bedrock_response(response)
            effective_model = _effective_model(call["model"])
            stats.add(effective_model, response.get("usage", {}))
            _store(call["model"], call["system"], call["user"], call["schema"], result)
            results[index] = result

        await asyncio.gather(*(one_bedrock(index, call) for index, call in pending))
        return results

    from openai import AsyncOpenAI

    client = AsyncOpenAI(max_retries=MAX_RETRIES)
    buckets = {
        model: TokenBucket(TPM_LIMITS.get(model, DEFAULT_TPM) * TPM_HEADROOM)
        for model in {call["model"] for _, call in pending}
    }

    async def one_openai(index: int, call: dict) -> None:
        async with sem:
            await buckets[call["model"]].acquire(estimate_tokens(call))
            response = await _with_backoff(lambda: client.chat.completions.create(
                **_request(
                    call["model"],
                    call["system"],
                    call["user"],
                    call["schema"],
                    call.get("max_tokens", MAX_OUTPUT_TOKENS),
                )
            ))
        result = json.loads(response.choices[0].message.content)
        stats.add(call["model"], response.usage)
        _store(call["model"], call["system"], call["user"], call["schema"], result)
        results[index] = result

    try:
        await asyncio.gather(*(one_openai(index, call) for index, call in pending))
        return results
    finally:
        await client.close()


def run_batch(calls: list[dict], concurrency: int = CONCURRENCY) -> list[dict]:
    """Run many calls concurrently; results come back in input order. Cached calls cost nothing."""
    return asyncio.run(_batch(calls, concurrency))


def uncached(calls: list[dict]) -> list[dict]:
    return [c for c in calls if cached(c["model"], c["system"], c["user"], c["schema"]) is None]
