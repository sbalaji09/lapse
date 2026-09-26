import json
import hashlib
from types import SimpleNamespace

from engine import config, llm


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class FakeBedrock:
    def __init__(self):
        self.requests = []

    def converse(self, **request):
        self.requests.append(request)
        answer = request["messages"][0]["content"][0]["text"]
        return {
            "output": {
                "message": {
                    "content": [{
                        "toolUse": {
                            "name": "return_json",
                            "input": {"answer": answer},
                        }
                    }]
                }
            },
            "usage": {"inputTokens": 11, "outputTokens": 3},
        }


def bedrock_mode(monkeypatch, tmp_path, client):
    monkeypatch.setattr(llm, "LLM_CACHE_DIR", tmp_path)
    monkeypatch.setenv("LAPSE_BACKEND", "aws")
    monkeypatch.delenv("LAPSE_OFFLINE", raising=False)
    monkeypatch.setattr(llm, "_bedrock_client", lambda: client, raising=False)


def test_bedrock_call_uses_converse_and_schema_tool(monkeypatch, tmp_path):
    client = FakeBedrock()
    bedrock_mode(monkeypatch, tmp_path, client)
    monkeypatch.setattr(config, "BEDROCK_MODEL_FAST", "amazon.nova-lite-test", raising=False)
    monkeypatch.setattr(llm, "stats", llm.Stats())

    result = llm.call_json("gpt-4.1-mini", "system rules", "patient note", SCHEMA, max_tokens=99)

    assert result == {"answer": "patient note"}
    assert llm.stats.input_tokens == {"amazon.nova-lite-test": 11}
    assert llm.stats.output_tokens == {"amazon.nova-lite-test": 3}
    request = client.requests[0]
    assert request["modelId"] == "amazon.nova-lite-test"
    assert request["system"] == [{"text": "system rules"}]
    assert request["inferenceConfig"] == {"temperature": 0, "maxTokens": 99}
    tool = request["toolConfig"]["tools"][0]["toolSpec"]
    assert tool["name"] == "return_json"
    assert tool["inputSchema"] == {"json": SCHEMA}
    assert request["toolConfig"]["toolChoice"] == {"tool": {"name": "return_json"}}


def test_bedrock_maps_the_verifier_model(monkeypatch):
    monkeypatch.setenv("LAPSE_BACKEND", "aws")
    monkeypatch.setattr(config, "BEDROCK_MODEL_VERIFY", "amazon.nova-pro-test", raising=False)

    assert llm._effective_model("gpt-4.1") == "amazon.nova-pro-test"


def test_openai_cache_key_stays_backward_compatible(monkeypatch):
    monkeypatch.setenv("LAPSE_BACKEND", "local")
    raw = json.dumps(["gpt-4.1-mini", "s", "u", SCHEMA], sort_keys=True, ensure_ascii=False)

    assert llm._key("gpt-4.1-mini", "s", "u", SCHEMA) == hashlib.sha256(raw.encode()).hexdigest()


def test_bedrock_cache_key_changes_with_model_id(monkeypatch, tmp_path):
    client = FakeBedrock()
    bedrock_mode(monkeypatch, tmp_path, client)
    monkeypatch.setattr(config, "BEDROCK_MODEL_FAST", "amazon.nova-lite-one", raising=False)

    first = llm.call_json("gpt-4.1-mini", "s", "same", SCHEMA)
    again = llm.call_json("gpt-4.1-mini", "s", "same", SCHEMA)
    monkeypatch.setattr(config, "BEDROCK_MODEL_FAST", "amazon.nova-lite-two")
    swapped = llm.call_json("gpt-4.1-mini", "s", "same", SCHEMA)

    assert first == again == swapped == {"answer": "same"}
    assert [request["modelId"] for request in client.requests] == [
        "amazon.nova-lite-one",
        "amazon.nova-lite-two",
    ]
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_bedrock_batch_preserves_order(monkeypatch, tmp_path):
    client = FakeBedrock()
    bedrock_mode(monkeypatch, tmp_path, client)
    monkeypatch.setattr(config, "BEDROCK_MODEL_FAST", "amazon.nova-lite-test", raising=False)
    calls = [
        {"model": "gpt-4.1-mini", "system": "s", "user": value, "schema": SCHEMA}
        for value in ("first", "second", "third")
    ]

    assert llm.run_batch(calls, concurrency=2) == [
        {"answer": "first"},
        {"answer": "second"},
        {"answer": "third"},
    ]
    assert len(client.requests) == 3


def test_local_backend_keeps_openai_as_explicit_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "LLM_CACHE_DIR", tmp_path)
    monkeypatch.setenv("LAPSE_BACKEND", "local")
    monkeypatch.delenv("LAPSE_OFFLINE", raising=False)

    class Completions:
        def create(self, **request):
            assert request["model"] == "gpt-4.1-mini"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"answer": "openai"})))],
                usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
            )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda **_: fake)
    assert llm.call_json("gpt-4.1-mini", "s", "u", SCHEMA) == {"answer": "openai"}


def test_bedrock_failure_does_not_silently_send_to_openai(monkeypatch, tmp_path):
    class BrokenBedrock:
        def converse(self, **_):
            raise RuntimeError("bedrock unavailable")

    bedrock_mode(monkeypatch, tmp_path, BrokenBedrock())
    monkeypatch.setattr(config, "BEDROCK_MODEL_FAST", "amazon.nova-lite-test", raising=False)
    import openai

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not fall through to OpenAI")),
    )

    try:
        llm.call_json("gpt-4.1-mini", "s", "u", SCHEMA)
    except RuntimeError as error:
        assert str(error) == "bedrock unavailable"
    else:
        raise AssertionError("Bedrock failure should be visible")
