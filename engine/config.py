"""Global settings for the Lapse engines. Everything time-dependent reads AS_OF_DATE, never the wall clock."""
from datetime import date
from pathlib import Path

AS_OF_DATE = date(2027, 2, 15)

# Fast model for extraction/parsing, stronger model for the verifier. Bedrock is a config swap.
MODEL_FAST = "gpt-4.1-mini"
MODEL_VERIFY = "gpt-4.1"

ACTIVE_RULE_PACK = "ca"

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "rules"
FACT_REGISTRY_PATH = RULES_DIR / "fact_registry.yaml"
DATA_DIR = ROOT / "data"
SYNTHEA_DIR = DATA_DIR / "synthea" / "fhir"
TRUTH_DIR = DATA_DIR / "truth"
EXTERNAL_DIR = DATA_DIR / "external"
FIXTURES_PATH = ROOT / "fixtures" / "golden_cases.json"
CACHE_DIR = ROOT / ".cache"
LLM_CACHE_DIR = CACHE_DIR / "llm"
DB_PATH = ROOT / "lapse.sqlite"


def rule_pack_path(state: str = ACTIVE_RULE_PACK) -> Path:
    return RULES_DIR / f"{state}.yaml"
