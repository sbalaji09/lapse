"""LLM plumbing: the rate pacer spends a per-minute budget without bursting past it, retries wait as long as
OpenAI asks, and a demo run with LAPSE_OFFLINE=1 can never reach the network."""
import asyncio

import pytest

from engine import llm


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


def test_bucket_paces_to_the_per_minute_budget():
    clock = FakeClock()
    bucket = llm.TokenBucket(per_minute=6000, clock=clock)

    async def spend(n_calls, tokens):
        for _ in range(n_calls):
            await bucket.acquire(tokens, sleep=clock.sleep)

    asyncio.run(spend(10, 600))          # the first minute's budget goes out immediately...
    assert clock.now == 0
    asyncio.run(spend(10, 600))          # ...the next 6000 tokens take a minute to refill
    assert clock.now == pytest.approx(60, rel=1e-6)


def test_bucket_never_deadlocks_on_an_oversized_request():
    clock = FakeClock()
    bucket = llm.TokenBucket(per_minute=100, clock=clock)
    asyncio.run(bucket.acquire(10_000, sleep=clock.sleep))
    assert clock.now == 0


def test_retry_after_honours_the_servers_hint():
    assert 0.3 <= llm.retry_after(Exception("Please try again in 259ms."), 0) <= 0.6
    assert 1.2 <= llm.retry_after(Exception("Please try again in 1.2s."), 0) <= 1.6
    assert llm.retry_after(Exception("server error"), 10) <= 25


def test_reservation_estimate_includes_the_output_cap():
    call = {"model": "m", "system": "x" * 300, "user": "y" * 300, "schema": {}, "max_tokens": 150}
    assert llm.estimate_tokens(call) == (600 + 2) // 3 + 150


def test_offline_mode_refuses_the_network(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "LLM_CACHE_DIR", tmp_path)
    monkeypatch.setenv("LAPSE_OFFLINE", "1")
    with pytest.raises(llm.CacheMiss):
        llm.call_json("gpt-4.1-mini", "system", "never cached", {"type": "object"})
    with pytest.raises(llm.CacheMiss):
        llm.run_batch([{"model": "gpt-4.1-mini", "system": "s", "user": "u", "schema": {"type": "object"}}])
