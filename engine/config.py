"""Global settings for the Lapse engines. Everything time-dependent reads AS_OF_DATE, never the wall clock."""
import os
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

AS_OF_DATE = date(2027, 2, 15)
# Timestamp stamped on every fact the batch pipeline records ("the overnight run before the demo morning").
PIPELINE_RUN_AT = datetime(2027, 2, 15, 6, 0, 0)
# Start of the 12-month window that notes are generated over. Channel A reads its own window from the rule pack.
LOOKBACK_START = date(2026, 2, 15)

# Seed for every generated artifact (Synthea was run with -s 42 too). Same seed -> same cohort, truth, notes.
SEED = 42

# Three synthetic clinics (phone numbers in the reserved fictional 555-01xx range); each cohort patient is assigned one, and its clinician signs attestations.
CLINICS = {
    "clinic-mission": {"name": "Mission Community Health", "clinician": "Dr. Anita Patel", "nurse": "Dana Whitfield, RN",
                       "phone": "(555) 010-0101"},
    "clinic-eastside": {"name": "Eastside Family Clinic", "clinician": "Dr. James Okafor", "nurse": "Luis Ortega, RN",
                        "phone": "(555) 010-0102"},
    "clinic-valley": {"name": "Valley Health Center", "clinician": "Dr. Maria Reyes", "nurse": "Grace Kim, RN",
                      "phone": "(555) 010-0103"},
}

# Local remains the default. AWS mode swaps only provider-specific settings; callers still use MODEL_FAST/VERIFY.
LAPSE_BACKEND = os.environ.get("LAPSE_BACKEND", "local")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

OPENAI_MODEL_FAST = os.environ.get("OPENAI_MODEL_FAST", "gpt-4.1-mini")
OPENAI_MODEL_VERIFY = os.environ.get("OPENAI_MODEL_VERIFY", "gpt-4.1")
BEDROCK_MODEL_FAST = os.environ.get("BEDROCK_MODEL_FAST", "amazon.nova-lite-v1:0")
BEDROCK_MODEL_VERIFY = os.environ.get("BEDROCK_MODEL_VERIFY", "amazon.nova-pro-v1:0")

MODEL_FAST = BEDROCK_MODEL_FAST if LAPSE_BACKEND == "aws" else OPENAI_MODEL_FAST
MODEL_VERIFY = BEDROCK_MODEL_VERIFY if LAPSE_BACKEND == "aws" else OPENAI_MODEL_VERIFY

ACTIVE_RULE_PACK = "ca"

RULES_DIR = ROOT / "rules"
FACT_REGISTRY_PATH = RULES_DIR / "fact_registry.yaml"
DATA_DIR = ROOT / "data"
SYNTHEA_DIR = DATA_DIR / "synthea" / "fhir"
CITY_COUNTY_PATH = DATA_DIR / "geo" / "ca_city_county.json"   # extracted from Synthea's geography/demographics.csv
QUALIFYING_CONDITIONS_PATH = DATA_DIR / "qualifying_conditions.yaml"
IMPAIRMENT_LIBRARY_PATH = DATA_DIR / "impairment_library.yaml"
LABELS_PATH = DATA_DIR / "truth" / "labels.jsonl"
TRUTH_DIR = DATA_DIR / "truth"
EXTERNAL_DIR = DATA_DIR / "external"
FIXTURES_PATH = ROOT / "fixtures" / "golden_cases.json"
CACHE_DIR = ROOT / ".cache"
LLM_CACHE_DIR = CACHE_DIR / "llm"
DB_PATH = ROOT / "lapse.sqlite"


def rule_pack_path(state: str = ACTIVE_RULE_PACK) -> Path:
    return RULES_DIR / f"{state}.yaml"
