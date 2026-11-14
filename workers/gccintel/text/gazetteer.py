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
# Words that put the following place name in the role of a location ("in the Strait of Hormuz",
# "في مضيق هرمز"), as opposed to an actor ("Tehran imposes restrictions", "Riyadh announced").
LOCATIVE_EN = frozenset({"in", "at", "near", "off", "outside", "inside", "over", "into", "around", "across", "on",
                         "toward", "towards", "targeting", "target", "targets", "targeted"})
LOCATIVE_AR = frozenset({"في", "فى", "قرب", "قباله", "داخل", "عند", "بالقرب", "شمال", "جنوب", "شرق", "غرب", "شمالي",
                         "جنوبي", "شرقي", "غربي", "على", "نحو", "تستهدف", "يستهدف", "استهدف", "استهدفت"})
_SKIP_BEFORE_PLACE = frozenset({"the", "a", "an", "of", "من"})
# "on", "over" and "على" also introduce topics ("sanctions on Israel", "positions on Gaza", "row over ...",
# "عقوبات على"). "on"/"على" place an event only after a word for an attack or a fall ("strike on Gaza hospital",
# "هجوم على"); "over" places one unless it follows a word for a dispute or an opinion.
_ON_AFTER_EN = frozenset({"attack", "attacks", "strike", "strikes", "airstrike", "airstrikes", "raid", "raids",
                          "assault", "assaults", "fire", "fired", "firing", "bombardment", "shelling", "drone", "drones",
                          "missile", "missiles", "rocket", "rockets", "crackdown", "landed", "lands", "fell", "falls",
                          "crashed", "incursion", "incursions", "ambush", "hit", "hits", "advance", "offensive", "march"})
_ON_AFTER_AR = frozenset({"هجوم", "هجمات", "اعتداء", "اعتداءات", "غاره", "غارات", "قصف", "ضربه", "ضربات", "صاروخ",
                          "صواريخ", "مسيره", "مسيرات", "سقط", "سقطت", "سقوط", "هجومها", "هجوما", "زحف"})
_OVER_TOPICAL = frozenset({"row", "rows", "anger", "outrage", "sanctions", "protest", "protests", "dispute", "disputes",
                           "concern", "concerns", "tension", "tensions", "talks", "fear", "fears", "criticism", "deal",
                           "crisis", "fury", "spat", "standoff", "feud", "split", "debate", "warning", "warnings", "probe",
                           "investigation", "lawsuit", "case", "charges", "ban", "pressure", "control", "sovereignty",
                           "influence", "rift", "differences", "disagreement", "negotiations", "complicity", "silence",
                           "stance", "position", "positions", "remarks", "comments"})
# A place followed by one of these is a topic ("in Gaza ceasefire talks", "over Gaza genocide").
_TOPIC_HEADS = frozenset({"genocide", "crisis", "talks", "deal", "ceasefire", "truce", "policy", "file", "envoy",
                          "negotiations", "sanctions", "issue", "question", "dossier", "peace"})
# A place followed (English) or preceded (Arabic) by one of these names an agreement, not a location
# ("Makkah pact", "Makkah Defence Pact", "Riyadh Agreement", "اتفاق الرياض", "إعلان جدة").
_NAME_HEADS = frozenset({"pact", "accord", "accords", "agreement", "declaration", "charter", "protocol", "convention",
                         "process", "format", "initiative", "formula", "communique"})
_NAME_QUALIFIERS = frozenset({"defence", "defense", "security", "peace", "trade", "nuclear", "cooperation", "mutual"})
_NAME_HEADS_AR = frozenset({"اتفاق", "اتفاقيه", "اعلان", "ميثاق", "مسار", "تفاهمات", "معاهده"})
_AR_CLITICS = ("وال", "بال", "فال", "ال", "و", "ب", "ف")


def _bare_ar(tok: str) -> str:
    for prefix in _AR_CLITICS:
        if tok.startswith(prefix) and len(tok) - len(prefix) >= 3:
            return tok[len(prefix):]
    return tok


def _locative(toks: list[str], i: int, end: int | None = None) -> bool:
    """Is the place name at toks[i:end] introduced by a locative word (skipping articles) and used as a
    location rather than as a topic?"""
    j = i - 1
    while j >= 0 and toks[j] in _SKIP_BEFORE_PLACE and i - j <= 2:
        j -= 1
    if j < 0 or not (toks[j] in LOCATIVE_EN or toks[j] in LOCATIVE_AR):
        return False
    word, before = toks[j], (toks[j - 1] if j >= 1 else "")
    if word == "on" and before not in _ON_AFTER_EN:
        return False
    if word == "على" and _bare_ar(before) not in _ON_AFTER_AR and before not in _ON_AFTER_AR:
        return False
    if word == "over" and before in _OVER_TOPICAL:
        return False
    return not (end is not None and end < len(toks) and toks[end] in _TOPIC_HEADS)


def _is_demonym(alias: str) -> bool:
    """'Israeli', 'Yemeni', 'Sudanese', 'Ethiopian', 'العراقي', 'اليمنية' ('Saudi' is also a short name)."""
    toks = match_form(alias).split()
    if len(toks) != 1 or toks[0] == "saudi":
        return False
    tok = toks[0]
    if tok.isascii():
        return bool(re.fullmatch(r"[a-z]+(?:i|ian|ese|ean)", tok))
    return tok.startswith("ال") and tok.endswith(("ي", "يه")) and len(tok) >= 5


def _origin(toks: list[str], i: int) -> bool:
    """'drone from Ethiopia', 'launched from the Red Sea': where something came from, not where it happened."""
    j = i - 1
    while j >= 0 and toks[j] in ("the", "a", "an") and i - j <= 2:
        j -= 1
    return j >= 0 and toks[j] == "from"


def _names_agreement(toks: list[str], i: int, end: int) -> bool:
    """'Makkah pact', 'Makkah Defence Pact', 'اتفاق الرياض': the place names an agreement."""
    nxt = toks[end:end + 2]
    if nxt and (nxt[0] in _NAME_HEADS or (len(nxt) == 2 and nxt[0] in _NAME_QUALIFIERS and nxt[1] in _NAME_HEADS)):
        return True
    return i >= 1 and (toks[i - 1] in _NAME_HEADS_AR or _bare_ar(toks[i - 1]) in _NAME_HEADS_AR)


# Places outside the coverage area that regional news names as where something happened ("drone activity
# over a UK air base", "protest in London", "talks in Geneva"). They have no coordinates and are not
# gazetteer entries: they only tell the analysis that an event happened elsewhere. ("US" is left out: it
# cannot be told apart from the pronoun "us" once text is lower-cased.)
EXTERNAL_PLACES = {
    "United Kingdom": ["United Kingdom", "UK", "U.K.", "Britain", "Great Britain", "England", "London", "Scotland",
                       "المملكة المتحدة", "بريطانيا", "لندن"],
    "United States": ["United States", "U.S.A.", "USA", "Washington", "New York", "الولايات المتحدة", "أمريكا",
                      "أميركا", "واشنطن", "نيويورك"],
    "France": ["France", "Paris", "فرنسا", "باريس"],
    "Germany": ["Germany", "Berlin", "ألمانيا", "برلين"],
    "Russia": ["Russia", "Moscow", "روسيا", "موسكو"],
    "China": ["China", "Beijing", "الصين", "بكين"],
    "India": ["India", "New Delhi", "الهند", "نيودلهي"],
    "Pakistan": ["Pakistan", "Islamabad", "Karachi", "باكستان", "إسلام آباد", "كراتشي"],
    "Türkiye": ["Turkey", "Türkiye", "Turkiye", "Ankara", "Istanbul", "تركيا", "أنقرة", "إسطنبول", "اسطنبول"],
    "Switzerland": ["Switzerland", "Geneva", "سويسرا", "جنيف"],
    "Austria": ["Austria", "Vienna", "النمسا", "فيينا"],
    "Italy": ["Italy", "Rome", "إيطاليا", "روما"],
    "Spain": ["Spain", "Madrid", "إسبانيا", "مدريد"],
    "Belgium": ["Belgium", "Brussels", "بلجيكا", "بروكسل"],
    "Netherlands": ["Netherlands", "The Hague", "Amsterdam", "هولندا", "لاهاي"],
    "Greece": ["Greece", "Athens", "اليونان", "أثينا"],
    "Cyprus": ["Cyprus", "Nicosia", "قبرص", "نيقوسيا"],
    "Ukraine": ["Ukraine", "Kyiv", "Kiev", "أوكرانيا", "كييف"],
    "Afghanistan": ["Afghanistan", "Kabul", "أفغانستان", "كابول"],
    "Azerbaijan": ["Azerbaijan", "Baku", "أذربيجان", "باكو"],
    "Libya": ["Libya", "Benghazi", "ليبيا", "بنغازي"],
    "Tunisia": ["Tunisia", "Tunis", "تونس"],
    "Algeria": ["Algeria", "Algiers", "الجزائر"],
    "Morocco": ["Morocco", "Rabat", "المغرب", "الرباط"],
    "Japan": ["Japan", "Tokyo", "اليابان", "طوكيو"],
    "South Korea": ["South Korea", "Seoul", "كوريا الجنوبية", "سيول"],
    "Canada": ["Canada", "Ottawa", "كندا", "أوتاوا"],
    "Australia": ["Australia", "Canberra", "أستراليا"],
    "Indonesia": ["Indonesia", "Jakarta", "إندونيسيا", "جاكرتا"],
    "Malaysia": ["Malaysia", "Kuala Lumpur", "ماليزيا", "كوالالمبور"],
    "Europe": ["Europe", "أوروبا"],
}
_EXTERNAL_INDEX: dict[str, list[tuple[tuple[str, ...], str]]] = defaultdict(list)
for _name, _aliases in EXTERNAL_PLACES.items():
    for _alias in _aliases:
        _toks = tuple(match_form(_alias).split())
        if _toks:
            _EXTERNAL_INDEX[_toks[0]].append((_toks, _name))

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
    locative: bool = False   # named after "in", "at", "near", "off", "في", "قرب" ... (where something happened)
    title_locative: bool = False
    only_named: bool = True  # every mention names an agreement ("Makkah pact"), none a location
    only_demonym: bool = True  # every mention is an adjective ("Israeli bombardment", "اليمني"), none the place
    only_origin: bool = True  # every mention is where something came from ("drone from Ethiopia")


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
    # How the place was chosen: "title-locative" ("... in Riyadh" in the headline), "locative" (in the text),
    # "title" (named in the headline), "mention" (named in the text) or "coordinates".
    basis: str = ""


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
        # Country adjectives listed as aliases ("Israeli", "Yemeni", "العراقي") find the country as an actor or
        # affected party, but "in Israeli bombardment of Gaza" does not happen in Israel.
        self._demonyms = {(e.id, alias) for e in entities if e.kind == "country"
                          for alias in (e.aliases or ()) if _is_demonym(alias)}

    def _spans(self, toks: list[str]) -> list[tuple[int, int, tuple[str, ...], GeoEntity, str, bool, bool, bool, bool]]:
        """Non-overlapping gazetteer matches in token order:
        (start, end, phrase, entity, alias, locative, names an agreement, country adjective, origin)."""
        spans = []
        for i, tok in enumerate(toks):
            candidates = set(arabic_variants(tok)) if any("؀" <= c <= "ۿ" for c in tok) else {tok}
            for cand in candidates:
                for phrase, entity, alias in self._index.get(cand, ()):
                    if tuple([cand, *toks[i + 1 : i + len(phrase)]]) == phrase:
                        end = i + len(phrase)
                        # Arabic "ب" attached to the place ("بمضيق هرمز") also means "in".
                        prefixed_in = cand != tok and tok.startswith(("ب", "وب", "فب"))
                        demonym = (entity.id, alias) in self._demonyms
                        origin = _origin(toks, i)
                        spans.append((i, end, phrase, entity, alias,
                                      not demonym and not origin and (prefixed_in or _locative(toks, i, end)),
                                      _names_agreement(toks, i, end), demonym, origin))
        # Longest match wins: "Gulf of Oman" must not also count as a mention of Oman.
        # Identical spans (one ambiguous alias, several places) are all kept for disambiguation.
        spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
        taken: set[int] = set()
        kept_spans: set[tuple[int, int]] = set()
        kept = []
        for span in spans:
            a, b = span[0], span[1]
            if (a, b) not in kept_spans and taken.intersection(range(a, b)):
                continue
            taken.update(range(a, b))
            kept_spans.add((a, b))
            kept.append(span)
        return sorted(kept, key=lambda s: s[0])

    def find(self, text: str, title: str = "") -> list[GeoMatch]:
        matches: dict[str, GeoMatch] = {}
        title_ids: set[str] = set()
        for source_text, is_title in ((title, True), (text, False)):
            for _a, _b, phrase, entity, alias, locative, named, demonym, origin in self._spans(
                    match_form(source_text).split()):
                owners = self._ambiguous.get(phrase)
                m = matches.get(entity.id)
                if m is None:
                    m = GeoMatch(entity=entity, alias=alias, count=0,
                                 ambiguous_with=sorted(owners - {entity.id}) if owners else [])
                    matches[entity.id] = m
                m.count += 1
                m.locative = m.locative or (locative and not named)
                m.title_locative = m.title_locative or (is_title and locative and not named)
                m.only_named = m.only_named and named
                m.only_demonym = m.only_demonym and demonym
                m.only_origin = m.only_origin and origin
                if is_title:
                    title_ids.add(entity.id)
        for eid in title_ids:
            matches[eid].in_title = True
        return self._disambiguate(list(matches.values()))

    def first_locative_place(self, text: str) -> tuple[str, bool] | None:
        """The first place `text` names as where something happened, as (name, outside the coverage area).
        "US responds to drone activity over UK air base used in Iran strikes" -> ("United Kingdom", True)."""
        toks = match_form(text).split()
        found: list[tuple[int, str, bool]] = [(a, entity.name, False)
                                              for a, _b, _p, entity, _al, locative, named, _d, _o in self._spans(toks)
                                              if locative and not named]
        for i, tok in enumerate(toks):
            for phrase, name in _EXTERNAL_INDEX.get(tok, ()):
                end = i + len(phrase)
                if tuple(toks[i:end]) == phrase and _locative(toks, i, end):
                    found.append((i, name, True))
        if not found:
            return None
        _, name, external = min(found, key=lambda f: (f[0], f[2]))
        return name, external

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
        basis = "coordinates"
        if matches:
            def score(m: GeoMatch) -> float:
                return (KIND_RANK.get(m.entity.kind, 0) * 10 + min(m.count, 5) * 2
                        + (4 if m.in_title else 0) + (1 if m.entity.is_gcc else 0))

            def within(ids: set[str]) -> list[GeoMatch]:
                return [m for m in candidates if m.entity.id in ids or any(a.id in ids for a in self.ancestors(m.entity))]
            # A place that only names an agreement ("Makkah pact"), appears only as an adjective ("Israeli
            # forces") or only as an origin ("drone from Ethiopia") is not a candidate while other places are named.
            candidates = ([m for m in matches if not (m.only_named or m.only_demonym or m.only_origin)]
                          or [m for m in matches if not (m.only_named or m.only_origin)]
                          or [m for m in matches if not m.only_named] or matches)
            # Places named as locations ("in", "near", "في" ...) come first, the headline's before the text's,
            # together with places inside them; a capital used for its government ("Tehran imposes ...") does
            # not place the event. Without locative words, places in the headline come before the rest.
            tiers = (("title-locative", {m.entity.id for m in candidates if m.title_locative}),
                     ("locative", {m.entity.id for m in candidates if m.locative}),
                     ("title", {m.entity.id for m in candidates if m.in_title}))
            basis, pool = next(((name, within(ids)) for name, ids in tiers if ids), ("mention", candidates))
            ranked = sorted(pool, key=score, reverse=True) + sorted([m for m in matches if m not in pool],
                                                                     key=score, reverse=True)
            best = ranked[0]
            # Drop a country match when a more specific place inside it was found.
            confidence = BASE_CONFIDENCE.get(best.entity.kind, 0.4)
            rivals = [m for m in ranked[1:] if m in pool
                      and KIND_RANK.get(m.entity.kind, 0) == KIND_RANK.get(best.entity.kind, 0)
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
                                        country, affected, notes, mentioned, "coordinates")

        assert best is not None
        e = best.entity
        country = e.country_code
        if country is None and country_hint:
            country = None  # maritime zone: keep country unset, affected_countries carries context
        if e.lat is None or e.lon is None:
            notes.append("Gazetteer entry has no coordinates; incident not placed on the map.")
        notes.append(f"Location from gazetteer entry '{e.name}' (±{int(e.precision_km or 0)} km).")
        return ResolvedLocation(e, e.lat, e.lon, KIND_TO_PRECISION.get(e.kind, "unknown"),
                                round(confidence, 2), f"gazetteer:{e.id}", country, affected, notes, mentioned, basis)
