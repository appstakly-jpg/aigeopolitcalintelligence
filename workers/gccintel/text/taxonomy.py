"""Deterministic event classification, sector tagging and actor matching (EN/AR)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .normalize import contains_phrase, match_form


@dataclass(frozen=True)
class EventType:
    key: str
    label: str
    default_severity: int
    risk_dimensions: tuple[str, ...]
    keywords_en: tuple[str, ...]
    keywords_ar: tuple[str, ...]
    review_required: bool = False
    enabled: bool = True

    @classmethod
    def from_row(cls, row: dict) -> EventType:
        return cls(
            key=row["key"], label=row["label"], default_severity=int(row.get("default_severity") or 2),
            risk_dimensions=tuple(row.get("risk_dimensions") or ()),
            keywords_en=tuple(row.get("keywords_en") or ()), keywords_ar=tuple(row.get("keywords_ar") or ()),
            review_required=bool(row.get("review_required")), enabled=row.get("enabled", True) is not False,
        )


@dataclass
class Classification:
    key: str
    score: float
    matched: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Actor:
    id: str
    name: str
    name_ar: str | None
    aliases: tuple[str, ...]
    actor_type: str
    country_code: str | None

    @classmethod
    def from_row(cls, row: dict) -> Actor:
        return cls(id=row["id"], name=row["name"], name_ar=row.get("name_ar"), aliases=tuple(row.get("aliases") or ()),
                   actor_type=row["actor_type"], country_code=row.get("country_code"))


def _weight(phrase: str) -> float:
    return 2.0 if len(match_form(phrase).split()) > 1 else 1.0


class Taxonomy:
    def __init__(self, event_types: list[EventType], sectors: dict[str, dict[str, list[str]]], actors: list[Actor]):
        self.event_types = {e.key: e for e in event_types if e.enabled}
        self.sectors = sectors
        self.actors = actors

    def classify(self, text: str, min_score: float = 2.0) -> list[Classification]:
        toks = match_form(text).split()
        results: list[Classification] = []
        for et in self.event_types.values():
            score, matched = 0.0, []
            for kw in (*et.keywords_en, *et.keywords_ar):
                if contains_phrase(toks, kw):
                    score += _weight(kw)
                    matched.append(kw)
            if score > 0:
                results.append(Classification(et.key, score, matched))
        results.sort(key=lambda c: c.score, reverse=True)
        strong = [c for c in results if c.score >= min_score]
        if strong:
            # Secondary categories with at least half the top score are kept too
            # ("drone attack on an oil facility" is both drone/missile and energy).
            floor = max(1.0, 0.5 * strong[0].score)
            return [c for c in results if c.score >= min(min_score, floor)]
        return results[:1]  # weak single signal: keep the top one, caller lowers confidence

    def sectors_for(self, text: str) -> list[str]:
        toks = match_form(text).split()
        out = []
        for sector, kws in self.sectors.items():
            if any(contains_phrase(toks, kw) for kw in (*kws.get("en", ()), *kws.get("ar", ()))):
                out.append(sector)
        return out

    def actors_in(self, text: str) -> list[Actor]:
        toks = match_form(text).split()
        found = []
        for actor in self.actors:
            names = [actor.name, *actor.aliases] + ([actor.name_ar] if actor.name_ar else [])
            if any(contains_phrase(toks, n) for n in names):
                found.append(actor)
        return found

    def dimensions_for(self, categories: list[str]) -> list[str]:
        dims: list[str] = []
        for c in categories:
            et = self.event_types.get(c)
            for d in (et.risk_dimensions if et else ()):
                if d not in dims:
                    dims.append(d)
        return dims
