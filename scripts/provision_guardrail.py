#!/usr/bin/env python3
"""Create or update the Bedrock Guardrail used by Lapse, then save its version locally."""
import argparse
import time

import boto3

from engine import guardrails
from engine.config import AWS_REGION


NAME = "lapse-patient-safety"
POLICY = {
    "description": (
        "Blocks patient-facing eligibility determinations and independently "
        "checks clinical assertions against source notes."
    ),
    "topicPolicyConfig": {
        "topicsConfig": [{
            "name": "Patient eligibility determinations",
            "definition": (
                "Patient-directed statements that decide or promise whether the patient is eligible, "
                "approved, denied, exempt, or will keep or lose health coverage. Allow process "
                "explanations and information requests."
            ),
            "examples": [
                "Rosa, you are eligible for Medi-Cal.",
                "You are exempt from the work requirement.",
                "Your coverage has been approved.",
                "Usted califica para Medi-Cal.",
                "Está exenta del requisito de trabajo.",
            ],
            "type": "DENY",
            "inputAction": "BLOCK",
            "outputAction": "BLOCK",
            "inputEnabled": True,
            "outputEnabled": True,
        }],
        "tierConfig": {"tierName": "CLASSIC"},
    },
    "contextualGroundingPolicyConfig": {
        "filtersConfig": [
            {"type": "GROUNDING", "threshold": 0.40, "action": "BLOCK", "enabled": True},
            {"type": "RELEVANCE", "threshold": 0.70, "action": "NONE", "enabled": True},
        ]
    },
    "blockedInputMessaging": (
        "I can explain the review process, but I cannot make or promise an eligibility decision."
    ),
    "blockedOutputsMessaging": (
        "I can help with the review process, but only the state can determine eligibility or coverage."
    ),
}


def find_guardrail(client, name: str) -> str | None:
    token = None
    while True:
        request = {"maxResults": 100}
        if token:
            request["nextToken"] = token
        response = client.list_guardrails(**request)
        match = next((item for item in response["guardrails"] if item["name"] == name), None)
        if match:
            return match["id"]
        token = response.get("nextToken")
        if not token:
            return None


def wait_ready(client, guardrail_id: str, version: str) -> None:
    for _ in range(30):
        response = client.get_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion=version,
        )
        if response["status"] == "READY":
            return
        if response["status"] == "FAILED":
            raise RuntimeError("; ".join(response.get("statusReasons", ["Guardrail creation failed"])))
        time.sleep(2)
    raise TimeoutError(f"Guardrail {guardrail_id} version {version} was not ready after 60 seconds")


def provision(name: str, region: str) -> tuple[str, str]:
    client = boto3.client("bedrock", region_name=region)
    guardrail_id = find_guardrail(client, name)
    if guardrail_id:
        client.update_guardrail(guardrailIdentifier=guardrail_id, name=name, **POLICY)
    else:
        guardrail_id = client.create_guardrail(name=name, **POLICY)["guardrailId"]
    wait_ready(client, guardrail_id, "DRAFT")

    version = client.create_guardrail_version(
        guardrailIdentifier=guardrail_id,
        description="Patient determination denial and claim grounding thresholds.",
    )["version"]
    wait_ready(client, guardrail_id, version)
    guardrails.save_local_config(guardrail_id, version, region)
    return guardrail_id, version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=NAME)
    parser.add_argument("--region", default=AWS_REGION)
    args = parser.parse_args()
    guardrail_id, version = provision(args.name, args.region)
    print(f"Guardrail ready: {guardrail_id} version {version} in {args.region}")
    print("Saved to .cache/bedrock_guardrail.json")


if __name__ == "__main__":
    main()
