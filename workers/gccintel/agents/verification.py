"""Verification Agent (E): corroboration and claim independence.

Pure, deterministic and explainable. Source COUNT is not corroboration: evidence is grouped
by an origin key (shared newsroom/network, wire copy, syndicated near-duplicate) and only
distinct origins count as independent. Official statements are attributable claims. Denials
from a different origin make an incident "disputed" and contradictory reporting stays
visible. "confirmed" is reserved for analyst confirmation unless policy allows otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RELIABLE = {"A", "B", "C"}
UNRELIABLE = {"D", "E", "F"}
ASSERTING_ROLES = {"original", "syndicated", "follow_up", "official_statement", "correction"}


@dataclass
class Evidence:
    article_id: str
    origin_key: str
    role: str
    stance: str                 # report | denial | correction | retraction | update | official_statement
    reliability: str            # A-F
    source_type: str
    publisher: str
    is_original: bool = True
    injection_flags: tuple[str, ...] = ()


@dataclass
class VerificationResult:
    status: str
    credibility: int
    independent_origins: list[str]
    official_origins: list[str]
    denying_origins: list[str]
    retracted_origins: list[str]
    source_count: int
    independent_count: int
    notes: list[str] = field(default_factory=list)
    needs_review_reasons: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "status": self.status, "credibility": self.credibility, "independent_origins": self.independent_origins,
            "official_origins": self.official_origins, "denying_origins": self.denying_origins,
            "retracted_origins": self.retracted_origins, "source_count": self.source_count,
            "independent_count": self.independent_count, "notes": self.notes,
            "needs_review_reasons": self.needs_review_reasons,
        }


def assess(evidence: list[Evidence], *, min_independent: int = 2, analyst_confirmed: bool = False,
           auto_confirm: bool = False) -> VerificationResult:
    notes: list[str] = []
    review: list[str] = []
    asserting = [e for e in evidence if e.role in ASSERTING_ROLES and e.stance not in ("denial", "retraction")]
    denying = [e for e in evidence if e.role == "contradicting" or e.stance == "denial"]
    retracting = [e for e in evidence if e.stance == "retraction" or e.role == "retraction"]

    origins: dict[str, list[Evidence]] = {}
    for e in asserting:
        origins.setdefault(e.origin_key, []).append(e)
    retracted_origins = sorted({e.origin_key for e in retracting})
    live_origins = {k: v for k, v in origins.items() if k not in retracted_origins}
    reliable_live = sorted(k for k, v in live_origins.items() if any(e.reliability in RELIABLE for e in v))
    official = sorted({e.origin_key for e in asserting if e.source_type == "government" or e.role == "official_statement"})
    deny_origins = sorted({e.origin_key for e in denying} - set(live_origins))
    deny_any = sorted({e.origin_key for e in denying})

    copies = len(asserting) - len(origins)
    if copies > 0:
        notes.append(f"{len(asserting)} supporting reports trace back to {len(origins)} independent origin(s); "
                     f"{copies} are copies or same-network reports and do not count as corroboration.")
    if official:
        notes.append("Official statements (" + ", ".join(official) + ") are attributable claims, not independent verification.")
    if any(e.injection_flags for e in evidence):
        review.append("Evidence contains instruction-like text (possible prompt injection).")

    independent = len(live_origins)
    if retracted_origins and not live_origins:
        status = "unverified"
        notes.append("All supporting reports have been retracted by their publishers.")
        review.append("Original reporting retracted.")
    elif deny_any:
        status = "disputed"
        notes.append("Conflicting reporting: denial(s) from " + ", ".join(deny_any) + ". Both versions are retained as evidence.")
        review.append("Contradictory reporting.")
        if retracted_origins:
            notes.append("Retracted by: " + ", ".join(retracted_origins) + ".")
    elif analyst_confirmed:
        status = "confirmed"
        notes.append("Confirmed by analyst review.")
    elif independent >= min_independent and reliable_live:
        status = "corroborated"
        if auto_confirm and independent >= 3 and len(reliable_live) >= 2:
            status = "confirmed"
            notes.append("Auto-confirmed by tenant policy (>= 3 independent origins, >= 2 reliable).")
    elif independent >= 1 and reliable_live:
        status = "single_source"
        if retracted_origins:
            notes.append("Some reports were retracted: " + ", ".join(retracted_origins) + ".")
    else:
        status = "unverified"
        if live_origins:
            notes.append("Only sources of low or unknown reliability report this.")

    if status != "disputed" and deny_origins:
        status = "disputed"

    credibility = {"confirmed": 1, "corroborated": 2, "disputed": 5, "unverified": 6}.get(status)
    if credibility is None:  # single source
        best = min((e.reliability for v in live_origins.values() for e in v), default="F")
        credibility = 3 if best in {"A", "B"} else 4
    return VerificationResult(
        status=status, credibility=credibility, independent_origins=sorted(live_origins),
        official_origins=official, denying_origins=deny_any, retracted_origins=retracted_origins,
        source_count=len(evidence), independent_count=independent, notes=notes, needs_review_reasons=review,
    )
