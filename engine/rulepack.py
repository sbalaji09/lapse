"""Rule packs (rules/<state>.yaml) and the three-valued evaluator shared by Channel A and the solver (A4).

`requires` is a list of terms that must ALL hold. A term is one or more atoms joined by `|` (ANY must hold).
An atom is a fact key (`ai_an`) or a comparison against a number (`hours_per_month >= 80`).

Facts are a dict of key -> value. Three-valued, and unknown never collapses into false:
  - key absent, or value Tri.unknown          -> unknown
  - bool / Tri.true / Tri.false               -> itself
  - value None                                -> known absent ("no release on record"): false
"""
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from engine.config import ACTIVE_RULE_PACK, RULES_DIR, rule_pack_path
from engine.checks import registry_keys
from engine.models import Tri

T, F, U = Tri.true, Tri.false, Tri.unknown
_ATOM = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*(?:(>=|<=|==|>|<)\s*(-?\d+(?:\.\d+)?))?\s*$")
_OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
        ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def tri(b: bool) -> Tri:
    return T if b else F


@dataclass(frozen=True)
class Atom:
    key: str
    op: str | None = None
    number: float | None = None

    def evaluate(self, facts: dict) -> Tri:
        if self.key not in facts:
            return U
        v = facts[self.key]
        if isinstance(v, Tri):
            if self.op is not None and v != U:
                raise TypeError(f"{self.key} is boolean but compared with {self.op}")
            return v
        if v is None:
            return F
        if self.op is None:
            if not isinstance(v, bool):
                raise TypeError(f"{self.key}={v!r} used as a boolean")
            return tri(v)
        if isinstance(v, bool):
            raise TypeError(f"{self.key} is boolean but compared with {self.op}")
        return tri(_OPS[self.op](v, self.number))

    def __str__(self) -> str:
        return self.key if self.op is None else f"{self.key} {self.op} {self.number:g}"


@dataclass(frozen=True)
class Term:
    """Atoms joined by `|`: true if any is true, false only if all are false."""
    atoms: tuple[Atom, ...]

    def evaluate(self, facts: dict) -> Tri:
        results = [a.evaluate(facts) for a in self.atoms]
        if T in results:
            return T
        return F if all(r == F for r in results) else U

    def keys(self) -> list[str]:
        return [a.key for a in self.atoms]

    def __str__(self) -> str:
        return " | ".join(map(str, self.atoms))


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str                         # "compliance" | "exemption"
    terms: tuple[Term, ...]           # all must hold
    state_ex_parte: dict | None       # how the state checks this rule from its own data (None = it cannot)

    def evaluate(self, facts: dict) -> Tri:
        results = [t.evaluate(facts) for t in self.terms]
        if F in results:
            return F
        return T if all(r == T for r in results) else U

    def unknown_terms(self, facts: dict) -> list[Term]:
        """Terms still unknown; empty when the rule is decided. The solver's raw material."""
        return [t for t in self.terms if t.evaluate(facts) == U]


def parse_term(text: str) -> Term:
    atoms = []
    for part in str(text).split("|"):
        m = _ATOM.match(part)
        if not m:
            raise ValueError(f"cannot parse rule atom {part!r} in {text!r}")
        key, op, num = m.groups()
        atoms.append(Atom(key, op, float(num) if num is not None else None))
    return Term(tuple(atoms))


@dataclass(frozen=True)
class RulePack:
    state: str
    version: str
    program: str
    citation: str
    lookback_months: int
    renewal_cadence_months: int
    notice_response_days: int
    self_attestation_allowed: dict
    rules: tuple[Rule, ...]           # compliance first, then exemptions, in file order
    state_visible_sources: frozenset[str]
    directory: Path

    @property
    def compliance(self) -> list[Rule]:
        return [r for r in self.rules if r.kind == "compliance"]

    @property
    def exemptions(self) -> list[Rule]:
        return [r for r in self.rules if r.kind == "exemption"]

    def rule(self, rule_id: str) -> Rule:
        return next(r for r in self.rules if r.id == rule_id)

    def code_list(self, relative: str) -> frozenset[str]:
        return read_code_list(self.directory / relative)

    def determine(self, facts: dict) -> tuple[str, list[str]]:
        """(status, satisfied rule ids). Compliance is checked first: people who meet it are never contacted."""
        satisfied = [r.id for r in self.rules if r.evaluate(facts) == T]
        if any(r.kind == "compliance" and r.id in satisfied for r in self.rules):
            return "compliant", satisfied
        if satisfied:
            return "exempt", satisfied
        return "not_determined", []


@cache
def read_code_list(path: Path) -> frozenset[str]:
    codes = set()
    for line in Path(path).read_text().splitlines():
        code = line.split("#", 1)[0].strip()
        if code:
            codes.add(code)
    return frozenset(codes)


@cache
def load_pack(state: str = ACTIVE_RULE_PACK) -> RulePack:
    path = rule_pack_path(state)
    raw = yaml.safe_load(path.read_text())
    rules = []
    for kind, key in (("compliance", "compliance"), ("exemption", "exemptions")):
        for r in raw[key]:
            rules.append(Rule(id=r["id"], kind=kind, terms=tuple(parse_term(t) for t in r["requires"]),
                              state_ex_parte=r.get("state_ex_parte")))
    pack = RulePack(
        state=raw["state"], version=raw["version"], program=raw["program"], citation=raw["citation"],
        lookback_months=raw["lookback_months"], renewal_cadence_months=raw["renewal_cadence_months"],
        notice_response_days=raw["notice_response_days"],
        self_attestation_allowed=raw.get("self_attestation_allowed", {}),
        rules=tuple(rules), state_visible_sources=frozenset(raw["state_visible_sources"]), directory=RULES_DIR,
    )
    _validate(pack)
    return pack


def _validate(pack: RulePack) -> None:
    keys = registry_keys()
    ids = [r.id for r in pack.rules]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{pack.state}: duplicate rule ids")
    for r in pack.rules:
        for t in r.terms:
            unknown = set(t.keys()) - keys
            if unknown:
                raise ValueError(f"{pack.state}/{r.id}: keys not in fact registry: {sorted(unknown)}")
        if r.state_ex_parte and "code_list" in r.state_ex_parte:
            if not pack.code_list(r.state_ex_parte["code_list"]):
                raise ValueError(f"{pack.state}/{r.id}: empty code list {r.state_ex_parte['code_list']}")
