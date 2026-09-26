"""Global settings for the Lapse engines. Everything time-dependent reads AS_OF_DATE, never the wall clock."""
from datetime import date
from pathlib import Path

AS_OF_DATE = date(2027, 2, 15)
# Start of the 12-month window that notes are generated over. Channel A reads its own window from the rule pack.
LOOKBACK_START = date(2026, 2, 15)

# Seed for every generated artifact (Synthea was run with -s 42 too). Same seed -> same cohort, truth, notes.
SEED = 42

# Three synthetic clinics; each cohort patient is assigned one, and its clinician signs attestations.
CLINICS = {
    "clinic-mission": {"name": "Mission Community Health", "clinician": "Dr. Anita Patel", "nurse": "Dana Whitfield, RN"},
    "clinic-eastside": {"name": "Eastside Family Clinic", "clinician": "Dr. James Okafor", "nurse": "Luis Ortega, RN"},
    "clinic-valley": {"name": "Valley Health Center", "clinician": "Dr. Maria Reyes", "nurse": "Grace Kim, RN"},
}

# Fast model for extraction/parsing, stronger model for the verifier. Bedrock is a config swap.
MODEL_FAST = "gpt-4.1-mini"
MODEL_VERIFY = "gpt-4.1"

ACTIVE_RULE_PACK = "ca"

ROOT = Path(__file__).resolve().parent.parent
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
