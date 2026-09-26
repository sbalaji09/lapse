"""Amazon Bedrock Guardrails for patient-message safety.

The deterministic eligibility engine never calls this module. Candidate
patient-facing output is checked against a denied topic before it is recorded.
"""
import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from engine import config


class GuardrailNotConfigured(RuntimeError):
    pass


class GuardrailCacheMiss(RuntimeError):
    pass


class GuardrailIntervened(ValueError):
    def __init__(self, assessment: dict):
        super().__init__(assessment["output"])
        self.assessment = assessment


_PRIVILEGED_PATTERNS = (
    (
        "Eligibility or exemption determination",
        re.compile(
            r"\b(?:exempt(?:ion|ed)?|eligib(?:le|ility)|ineligib(?:le|ility)|"
            r"qualif(?:y|ies|ied)|exent[oa]|exenci[oó]n|elegible|inelegible|"
            r"elegibilidad|califica|no\s+califica)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Approval or denial determination",
        re.compile(
            r"\b(?:approv(?:e|ed|al)|den(?:y|ied|ial)|aprob(?:ado|ada|aci[oó]n)|"
            r"deneg(?:ado|ada|aci[oó]n))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Guaranteed coverage outcome",
        re.compile(
            r"\b(?:will|won't|will\s+not|guaranteed\s+to|va\s+a)\s+"
            r"(?:keep|lose|retain|continue|mantener|perder|conservar)"
            r"(?:\s+(?:your|su))?\s+(?:medi-cal\s+)?(?:coverage|cobertura)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Internal case status",
        re.compile(
            r"\b(?:one[_ -]?away|provable|no[_ -]?path|safe\s+bucket|"
            r"waiting[_ -]?(?:patient|clinician)|attestation[_ -]?ready|"
            r"(?:your|rosa(?:'s)?|su)\s+"
            r"(?:(?:case|coverage|application|renewal|caso|cobertura|solicitud|"
            r"renovaci[oó]n)\s+)?(?:current\s+)?(?:status|estado)|"
            r"estado\s+de\s+su\s+(?:caso|cobertura|solicitud|renovaci[oó]n))\b",
            re.IGNORECASE,
        ),
    ),
)


def check_local_patient_message(text: str) -> dict:
    """Enforce the patient-message invariant without relying on a provider."""
    detected = [
        {
            "name": name,
            "type": "DENY",
            "action": "BLOCKED",
            "detected": True,
        }
        for name, pattern in _PRIVILEGED_PATTERNS
        if pattern.search(text)
    ]
    allowed = not detected
    return {
        "allowed": allowed,
        "action": "NONE" if allowed else "GUARDRAIL_INTERVENED",
        "output": (
            ""
            if allowed
            else "This message contains privileged eligibility or case-status information and cannot be sent."
        ),
        "topics": detected,
        "provider": "Lapse deterministic outbound policy",
    }


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
            "or run python -m scripts.provision_guardrail"
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
    try:
        import boto3
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "boto3 is required when Amazon Bedrock Guardrails is configured; "
            "install the project requirements"
        ) from error
    return boto3.client("bedrock-runtime", region_name=region)


def _cache_path(guardrail_id: str, version: str, text: str) -> Path:
    raw = json.dumps([guardrail_id, version, text], ensure_ascii=False)
    return config.GUARDRAIL_CACHE_DIR / f"{hashlib.sha256(raw.encode()).hexdigest()}.json"


def _apply(text: str) -> dict:
    guardrail_id, version, region = _settings()
    path = _cache_path(guardrail_id, version, text)
    if path.exists():
        return json.loads(path.read_text())
    if os.environ.get("LAPSE_OFFLINE") == "1":
        raise GuardrailCacheMiss("LAPSE_OFFLINE=1 and no cached Bedrock Guardrail assessment")

    response = _runtime_client(region).apply_guardrail(
        guardrailIdentifier=guardrail_id,
        guardrailVersion=version,
        source="OUTPUT",
        content=[{"text": {"text": text}}],
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
    response = _apply(text)
    topics = []
    for assessment in response.get("assessments", []):
        topics.extend(assessment.get("topicPolicy", {}).get("topics", []))
    output = " ".join(item.get("text", "") for item in response.get("outputs", [])).strip()
    return {
        "allowed": response["action"] == "NONE",
        "action": response["action"],
        "output": output,
        "topics": topics,
        "provider": "Amazon Bedrock Guardrails",
    }


def enforce_patient_message(text: str) -> dict:
    local_assessment = check_local_patient_message(text)
    if not local_assessment["allowed"]:
        raise GuardrailIntervened(local_assessment)

    if not configured():
        return local_assessment

    assessment = check_patient_message(text)
    if not assessment["allowed"]:
        raise GuardrailIntervened(assessment)
    return assessment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", help="candidate patient-facing message")
    args = parser.parse_args()
    print(json.dumps(check_patient_message(args.text), indent=2))


if __name__ == "__main__":
    main()
