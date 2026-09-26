"""Amazon Bedrock Guardrails: patient-message safety and contextual grounding.

The deterministic eligibility engine never calls this module. Patient-facing
output is checked as a denied topic; clinical assertions are checked against
their source note for the independent grounding report.
"""
import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import boto3

from engine import config


class GuardrailNotConfigured(RuntimeError):
    pass


class GuardrailCacheMiss(RuntimeError):
    pass


class GuardrailIntervened(ValueError):
    def __init__(self, assessment: dict):
        super().__init__(assessment["output"])
        self.assessment = assessment


def _settings() -> tuple[str, str, str]:
    guardrail_id = os.environ.get("BEDROCK_GUARDRAIL_ID") or config.BEDROCK_GUARDRAIL_ID
    version = os.environ.get("BEDROCK_GUARDRAIL_VERSION") or config.BEDROCK_GUARDRAIL_VERSION
    region = os.environ.get("AWS_REGION", config.AWS_REGION)
    if (not guardrail_id or not version) and config.GUARDRAIL_CONFIG_PATH.exists():
        local = json.loads(config.GUARDRAIL_CONFIG_PATH.read_text())
        guardrail_id = guardrail_id or local.get("guardrail_id")
        version = version or local.get("version")
        region = local.get("region", region)
    if not guardrail_id or not version:
        raise GuardrailNotConfigured(
            "set BEDROCK_GUARDRAIL_ID and BEDROCK_GUARDRAIL_VERSION, "
            "or run scripts/provision_guardrail.py"
        )
    return guardrail_id, version, region


def configured() -> bool:
    try:
        _settings()
        return True
    except GuardrailNotConfigured:
        return False


def save_local_config(guardrail_id: str, version: str, region: str) -> None:
    config.GUARDRAIL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.GUARDRAIL_CONFIG_PATH.write_text(json.dumps({
        "guardrail_id": guardrail_id,
        "version": version,
        "region": region,
    }, indent=2) + "\n")


def _runtime_client(region: str):
    return boto3.client("bedrock-runtime", region_name=region)


def _cache_path(guardrail_id: str, version: str, source: str, content: list[dict]) -> Path:
    raw = json.dumps([guardrail_id, version, source, content], sort_keys=True, ensure_ascii=False)
    return config.GUARDRAIL_CACHE_DIR / f"{hashlib.sha256(raw.encode()).hexdigest()}.json"


def _apply(source: str, content: list[dict]) -> dict:
    guardrail_id, version, region = _settings()
    path = _cache_path(guardrail_id, version, source, content)
    if path.exists():
        return json.loads(path.read_text())
    if os.environ.get("LAPSE_OFFLINE") == "1":
        raise GuardrailCacheMiss("LAPSE_OFFLINE=1 and no cached Bedrock Guardrail assessment")

    response = _runtime_client(region).apply_guardrail(
        guardrailIdentifier=guardrail_id,
        guardrailVersion=version,
        source=source,
        content=content,
        outputScope="FULL",
    )
    response.pop("ResponseMetadata", None)
    config.GUARDRAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=config.GUARDRAIL_CACHE_DIR, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(response, handle, ensure_ascii=False)
    os.replace(handle.name, path)
    return response


def check_patient_message(text: str) -> dict:
    """Check a candidate patient-facing output for a denied determination."""
    response = _apply("OUTPUT", [{"text": {"text": text}}])
    topics = []
    for assessment in response.get("assessments", []):
        topics.extend(assessment.get("topicPolicy", {}).get("topics", []))
    output = " ".join(item.get("text", "") for item in response.get("outputs", [])).strip()
    return {
        "allowed": response["action"] == "NONE",
        "action": response["action"],
        "output": output,
        "topics": topics,
    }


def enforce_patient_message(text: str) -> dict:
    assessment = check_patient_message(text)
    if not assessment["allowed"]:
        raise GuardrailIntervened(assessment)
    return assessment


def check_grounding(source: str, query: str, assertion: str) -> dict:
    """Score one assertion against its full source and extraction standard."""
    response = _apply("OUTPUT", [
        {"text": {"text": source, "qualifiers": ["grounding_source"]}},
        {"text": {"text": query, "qualifiers": ["query"]}},
        {"text": {"text": assertion, "qualifiers": ["guard_content"]}},
    ])
    filters = {}
    for assessment in response.get("assessments", []):
        for item in assessment.get("contextualGroundingPolicy", {}).get("filters", []):
            filters[item["type"].lower()] = {
                "score": item["score"],
                "threshold": item["threshold"],
                "action": item["action"],
            }
    if set(filters) != {"grounding", "relevance"}:
        raise ValueError("Bedrock response did not contain grounding and relevance assessments")
    return {
        "kept": all(item["action"] == "NONE" for item in filters.values()),
        "action": response["action"],
        **filters,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", help="candidate patient-facing message")
    args = parser.parse_args()
    print(json.dumps(check_patient_message(args.text), indent=2))


if __name__ == "__main__":
    main()
