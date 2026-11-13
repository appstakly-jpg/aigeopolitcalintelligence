"""Gazetteer matching and location resolution (English + Arabic).

Coordinates come ONLY from gazetteer entries (with their stated precision) or from
coordinates explicitly written in the source text. Ambiguous names (e.g. Arabic "عمان",
which is both Oman and Amman without diacritics) are resolved from context and the
reduced confidence is recorded.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .normalize import arabic_variants, match_form

KIND_RANK = {
    "facility": 6, "port": 6, "border_crossing": 5, "city": 5, "admin1": 4,
    "maritime_zone": 3, "corridor": 3, "country": 1,
}
KIND_TO_PRECISION = {
    "facility": "facility", "port": "port", "border_crossing": "point", "city": "city", "admin1": "admin1",
    "maritime_zone": "maritime_zone", "corridor": "corridor", "country": "country",
}
BASE_CONFIDENCE = {
    "facility": 0.85, "port": 0.85, "border_crossing": 0.8, "city": 0.8, "admin1": 0.65,
    "maritime_zone": 0.6, "corridor": 0.6, "country": 0.5,
}
GCC_CODES = {"SA", "AE", "QA", "KW", "BH", "OM"}


@dataclass(frozen=True)
class GeoEntity:
    id: str
    kind: str
    name: str
    name_ar: str | None
    aliases: tuple[str, ...]
    country_code: str | None
    parent_id: str | None
    lat: float | None
    lon: float | None
    precision_km: float | None
    is_gcc: bool
    region: str

    @classmethod
    def from_row(cls, row: dict) -> GeoEntity:
        return cls(
            id=row["id"], kind=row["kind"], name=row["name"], name_ar=row.get("name_ar"),
            aliases=tuple(row.get("aliases") or ()), country_code=row.get("country_code"),
            parent_id=row.get("parent_id"), lat=row.get("lat"), lon=row.get("lon"),
            precision_km=float(row["precision_km"]) if row.get("precision_km") is not None else None,
            is_gcc=bool(row.get("is_gcc")), region=row.get("region", "other"),
        )


@dataclass
class GeoMatch:
    entity: GeoEntity
    alias: str
    count: int = 1
    in_title: bool = False
    ambiguous_with: list[str] = field(default_factory=list)


@dataclass
class ResolvedLocation:
    entity: GeoEntity | None
    lat: float | None
    lon: float | None
    precision: str
    confidence: float
    source: str | None
    country_code: str | None
    affected_countries: list[str]
    notes: list[str]
    mentioned_entity_ids: list[str]


_COORD_RE = re.compile(
    r"(?P<lat>\d{1,2}(?:\.\d+)?)\s*°?\s*(?P<ns>[NS])[\s,;/]+(?P<lon>\d{1,3}(?:\.\d+)?)\s*°?\s*(?P<ew>[EW])\b"
)


class Gazetteer:
    def __init__(self, entities: list[GeoEntity]):
        self.entities = {e.id: e for e in entities}
        index: dict[str, list[tuple[tuple[str, ...], GeoEntity, str]]] = defaultdict(list)
        phrase_owners: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for e in entities:
            names = {e.name, *(e.aliases or ())}
            if e.name_ar:
                names.add(e.name_ar)
            for alias in names:
                toks = tuple(match_form(alias).split())
                if not toks:
                    continue
                phrase_owners[toks].add(e.id)
                index[toks[0]].append((toks, e, alias))
        self._index = index
        self._ambiguous = {p: ids for p, ids in phrase_owners.items() if len(ids) > 1}

    def find(self, text: str, title: str = "") -> list[GeoMatch]:
        matches: dict[str, GeoMatch] = {}
        title_ids: set[str] = set()
        for source_text, is_title in ((title, True), (text, False)):
            toks = match_form(source_text).split()
            spans = []
            for i, tok in enumerate(toks):
                candidates = set(arabic_variants(tok)) if any("؀" <= c <= "ۿ" for c in tok) else {tok}
                for cand in candidates:
                    for phrase, entity, alias in self._index.get(cand, ()):
                        if tuple([cand, *toks[i + 1 : i + len(phrase)]]) == phrase:
                            spans.append((i, i + len(phrase), phrase, entity, alias))
            # Longest match wins: "Gulf of Oman" must not also count as a mention of Oman.
            # Identical spans (one ambiguous alias, several places) are all kept for disambiguation.
            spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
            taken: set[int] = set()
            kept_spans: set[tuple[int, int]] = set()
            for a, b, phrase, entity, alias in spans:
                if (a, b) not in kept_spans and taken.intersection(range(a, b)):
                    continue
                taken.update(range(a, b))
                kept_spans.add((a, b))
                owners = self._ambiguous.get(phrase)
                m = matches.get(entity.id)
                if m is None:
                    m = GeoMatch(entity=entity, alias=alias, count=0,
                                 ambiguous_with=sorted(owners - {entity.id}) if owners else [])
                    matches[entity.id] = m
                m.count += 1
                if is_title:
                    title_ids.add(entity.id)
        for eid in title_ids:
            matches[eid].in_title = True
        return self._disambiguate(list(matches.values()))

    def _disambiguate(self, matches: list[GeoMatch]) -> list[GeoMatch]:
        """For ambiguous aliases keep the candidate whose country is otherwise mentioned;
        fall back to the GCC candidate (platform focus) with an ambiguity note."""
        mentioned_countries = {m.entity.country_code for m in matches if not m.ambiguous_with}
        by_id = {m.entity.id: m for m in matches}
        drop: set[str] = set()
        for m in matches:
            if not m.ambiguous_with or m.entity.id in drop:
                continue
            group = [m] + [by_id[i] for i in m.ambiguous_with if i in by_id]
            if len(group) < 2:
                continue
            supported = [g for g in group if g.entity.country_code in mentioned_countries]
            winner = supported[0] if len(supported) == 1 else next((g for g in group if g.entity.is_gcc), group[0])
            for g in group:
                if g is not winner:
                    drop.add(g.entity.id)
        return [m for m in matches if m.entity.id not in drop]

    def ancestors(self, entity: GeoEntity) -> list[GeoEntity]:
        out, cur = [], entity
        seen = set()
        while cur.parent_id and cur.parent_id in self.entities and cur.parent_id not in seen:
            seen.add(cur.parent_id)
            cur = self.entities[cur.parent_id]
            out.append(cur)
        return out

    def resolve(self, text: str, title: str = "", country_hint: list[str] | None = None) -> ResolvedLocation:
        notes: list[str] = []
        matches = self.find(text, title)
        coord = _COORD_RE.search(text or "")
        affected: list[str] = []
        for m in matches:
            for e in [m.entity, *self.ancestors(m.entity)]:
                if e.country_code and e.country_code not in affected:
                    affected.append(e.country_code)
        mentioned = [m.entity.id for m in matches]

        if not matches and not coord:
            return ResolvedLocation(None, None, None, "unknown", 0.0, None,
                                    None, [], ["No gazetteer location found in the text."], [])

        best: GeoMatch | None = None
        if matches:
            def score(m: GeoMatch) -> float:
                return (KIND_RANK.get(m.entity.kind, 0) * 10 + min(m.count, 5) * 2
                        + (4 if m.in_title else 0) + (1 if m.entity.is_gcc else 0))
            ranked = sorted(matches, key=score, reverse=True)
            best = ranked[0]
            # Drop a country match when a more specific place inside it was found.
            confidence = BASE_CONFIDENCE.get(best.entity.kind, 0.4)
            rivals = [m for m in ranked[1:]
                      if KIND_RANK.get(m.entity.kind, 0) == KIND_RANK.get(best.entity.kind, 0)
                      and m.entity.country_code != best.entity.country_code
                      and best.entity.id not in [a.id for a in self.ancestors(m.entity)]
                      and m.entity.id not in [a.id for a in self.ancestors(best.entity)]]
            if rivals:
                confidence -= 0.2
                notes.append("Several places of equal specificity are mentioned: "
                             + ", ".join(r.entity.name for r in rivals[:3]) + f"; chose {best.entity.name}.")
            if best.ambiguous_with:
                confidence -= 0.15
                notes.append(f"Place name '{best.alias}' is ambiguous; resolved to {best.entity.name} from context.")
            confidence = max(0.1, min(0.95, confidence))
        if coord:
            lat = float(coord.group("lat")) * (1 if coord.group("ns") == "N" else -1)
            lon = float(coord.group("lon")) * (1 if coord.group("ew") == "E" else -1)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                notes.append("Coordinates explicitly reported in the source text.")
                country = best.entity.country_code if best else None
                return ResolvedLocation(best.entity if best else None, lat, lon, "point", 0.9, "source-reported",
                                        country, affected, notes, mentioned)

        assert best is not None
        e = best.entity
        country = e.country_code
        if country is None and country_hint:
            country = None  # maritime zone: keep country unset, affected_countries carries context
        if e.lat is None or e.lon is None:
            notes.append("Gazetteer entry has no coordinates; incident not placed on the map.")
        notes.append(f"Location from gazetteer entry '{e.name}' (±{int(e.precision_km or 0)} km).")
        return ResolvedLocation(e, e.lat, e.lon, KIND_TO_PRECISION.get(e.kind, "unknown"),
                                round(confidence, 2), f"gazetteer:{e.id}", country, affected, notes, mentioned)
