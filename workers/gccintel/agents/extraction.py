"""Event Extraction Agent (D).

Deterministic rule-based extraction always runs. When an LLM is configured it adds
translation, a neutral summary and extra recall; its output is validated against the
source text and the reference data before use:
* categories must exist in the taxonomy,
* places are resolved through the gazetteer only (the model never supplies coordinates),
* casualty numbers must literally appear in the source text,
* attributions must appear in the source text,
* actors are organisations from the actor list (no private individuals).
Severity, verification status and review requirements are NOT extracted - they are computed
later by deterministic code.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ..llm.base import LLMError, LLMUnavailable
from ..llm.service import LLMService
from ..log import get_logger
from ..text import injection
from ..text.gazetteer import GCC_CODES, Gazetteer, ResolvedLocation
from ..text.normalize import clean_text, match_form, normalize_arabic, split_sentences
from ..text.taxonomy import Taxonomy

log = get_logger(__name__)

EXTRACTOR_VERSION = "rules-v1"
SECONDARY_GCC_RELEVANT = {"drone_missile", "maritime_incident", "shipping_disruption", "energy_disruption",
                          "cross_border_attack", "sanctions", "terrorism", "border_restrictions"}
MARITIME_REGIONS = {"gulf_waters", "red_sea", "arabian_sea"}
CONFLICT_NEIGHBOURS = {"yemen", "iraq"}  # theatres bordering GCC states

_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "eleven": 11, "twelve": 12, "a": 1, "an": 1}
_NUM = r"(\d{1,4}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_CAS_EN = [
    (re.compile(rf"\b(?:at least |up to |some )?{_NUM}\s+(?:people|persons|civilians|soldiers|workers|crew members|crew|"
                rf"sailors|policemen|police officers|troops|others|migrants)?\s*(?:were |have been |had been )?"
                rf"(killed|dead|died|injured|wounded|hurt)\b", re.I), 1, 2),
    (re.compile(rf"\b(killing|injuring|wounding)\s+(?:at least\s+)?{_NUM}\b", re.I), 2, 1),
    (re.compile(rf"\b(killed|kills|injured|injures|wounded|wounds)\s+(?:at least\s+)?{_NUM}\s+(?:people|persons|"
                rf"civilians|soldiers|workers|crew members|sailors|policemen|police officers|troops|others|migrants)\b",
                re.I), 2, 1),
    (re.compile(rf"\b(?:death toll|toll)\s+(?:rose|climbed|reached|stands at|stood at|is)\s+(?:to\s+)?{_NUM}\b", re.I), 1, None),
]
_ZERO_CAS_EN = re.compile(r"\bno (?:casualties|injuries|deaths|fatalities)\b|\bno one was (?:hurt|injured|killed)\b|"
                          r"\bwithout (?:casualties|injuries)\b", re.I)
_CAS_AR = [
    (re.compile(r"(مقتل|استشهاد|وفاه|وفاة)\s+(\d{1,4})"), "killed", 2),
    (re.compile(r"(\d{1,4})\s+(قتلي|قتلى|قتيلا|قتيل|شهداء|شهيدا|وفيات)"), "killed", 1),
    (re.compile(r"(اصابه|إصابة|جرح)\s+(\d{1,4})"), "injured", 2),
    (re.compile(r"(\d{1,4})\s+(جرحي|جرحى|مصابين|مصابا|اصابات|إصابات|جريحا)"), "injured", 1),
]
_ZERO_CAS_AR = re.compile(r"(دون (?:وقوع )?(?:اصابات|إصابات)|لا (?:اصابات|إصابات)|لم (?:تسفر|يسفر) عن (?:اي |أي )?(?:اصابات|إصابات|خسائر))")

_WIRES = [
    ("reuters", re.compile(r"\(Reuters\)|\bReuters reported\b|رويترز")),
    ("afp", re.compile(r"\(AFP\)|\bAFP reported\b|فرانس برس|أ ف ب|ا ف ب")),
    ("ap", re.compile(r"\(AP\)|\bAssociated Press\b|أسوشيتد برس")),
    ("bloomberg", re.compile(r"\(Bloomberg\)")),
    ("spa", re.compile(r"\(SPA\)|\(واس\)|وكالة الأنباء السعودية")),
    ("wam", re.compile(r"\(WAM\)|\(وام\)|وكالة أنباء الإمارات")),
    ("qna", re.compile(r"\(QNA\)|\(قنا\)|وكالة الأنباء القطرية")),
    ("kuna", re.compile(r"\(KUNA\)|\(كونا\)|وكالة الأنباء الكويتية")),
    ("bna", re.compile(r"\(BNA\)|\(بنا\)|وكالة أنباء البحرين")),
    ("ona", re.compile(r"\(ONA\)|\(العمانية\)|وكالة الأنباء العمانية")),
    ("irna", re.compile(r"\(IRNA\)|\(إرنا\)")),
    ("demo-wire", re.compile(r"\(Demo Wire\)|\bDemo Wire Service\b")),
]
_DENIAL = re.compile(r"\b(denied|denies|deny|dismissed (?:the )?reports?|rejected (?:the )?(?:claims?|reports?)|false reports?|"
                     r"baseless|fabricated|no (?:such )?(?:attack|explosion|incident|blast) (?:took place|occurred|happened))\b|"
                     r"(نفي|نفت|ينفي|تنفي|عار عن الصحه|عاريه عن الصحه|لا صحه)", re.I)
_CORRECTION = re.compile(r"(^\s*(\[[^\]]*\]\s*)?(correction|corrected|clarification)\b)|\bcorrects? (?:an |its )?earlier report\b|"
                         r"\bupdated to correct\b|(تصحيح)", re.I)
_RETRACTION = re.compile(r"\b(retract(?:s|ed|ion)?|withdr(?:aw|ew|awn) (?:the |its |an earlier )?(?:report|story|article))\b|"
                         r"(سحب الخبر)", re.I)
_OFFICIAL = re.compile(r"\b(ministry|minister|government|authorities|armed forces|military|spokes(?:person|man|woman)|"
                       r"police|coast guard|civil defen[cs]e|royal court|presidency|interior|defen[cs]e|official statement|"
                       r"state news agency)\b|(وزاره|وزارة|وزير|الحكومه|الحكومة|السلطات|القوات المسلحه|القوات المسلحة|الجيش|"
                       r"المتحدث|الناطق|الشرطه|الشرطة|خفر السواحل|الدفاع المدني|بيان رسمي|مصدر رسمي)", re.I)
_ATTR_EN = [
    re.compile(r"\baccording to (?:the |a |an )?(?P<src>[A-Za-z][\w'’\-]*(?:\s+(?:of|for|and|the|al-|[A-Za-z][\w'’\-]*)){0,6}?)"
               r"(?=[,.;]|\s+(?:which|who|that|said|in|on)\b|$)"),
    re.compile(r"(?P<src>\b(?:the\s+)?[A-Z][\w'’\-]*(?:\s+(?:of|for|[A-Z][\w'’\-]*)){0,5}|authorities|officials|residents|witnesses|"
               r"a spokesperson|a spokesman|the ministry|the government|police)\s+(?:said|says|stated|announced|confirmed|"
               r"claimed|denied|reported)\b"),
    re.compile(r"\b(?:said|says|stated|announced|confirmed|claimed|denied)\s+(?:the\s+)?(?P<src>[A-Z][\w'’\-]*(?:\s+(?:of|for|"
               r"[A-Z][\w'’\-]*)){0,6})"),
]
_ATTR_AR = [
    re.compile(r"(?:بحسب|حسب|وفقا ل|وفقاً ل|نقلا عن|نقلاً عن)\s*(?P<src>[^،,.:]{3,60})"),
    re.compile(r"(?:قال|قالت|أعلن|أعلنت|اعلن|اعلنت|ذكر|ذكرت|أفاد|أفادت|أكد|أكدت|نفى|نفت)\s+(?P<src>[^،,.:]{3,60})"),
]
_MONTHS_EN = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"], start=1)}
_MONTHS_AR = {"يناير": 1, "فبراير": 2, "مارس": 3, "ابريل": 4, "أبريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7,
              "اغسطس": 8, "أغسطس": 8, "سبتمبر": 9, "اكتوبر": 10, "أكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12}
_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
             "الاثنين": 0, "الثلاثاء": 1, "الأربعاء": 2, "الاربعاء": 2, "الخميس": 3, "الجمعة": 4, "الجمعه": 4,
             "السبت": 5, "الأحد": 6, "الاحد": 6}


@dataclass
class ClaimDraft:
    claim_type: str
    text: str
    attributed_to: str | None = None
    is_official: bool = False
    stance: str = "asserts"
    numeric_value: float | None = None
    numeric_unit: str | None = None
    confidence: float = 0.6


@dataclass
class Extraction:
    is_relevant: bool
    relevance: float
    relevance_reason: str
    categories: list[str]
    category_scores: dict[str, float]
    primary_category: str | None
    title: str
    title_en: str | None
    summary_en: str | None
    location: dict
    actors: list[str]
    sectors: list[str]
    casualties_killed: int | None
    casualties_injured: int | None
    casualty_note: str | None
    claims: list[ClaimDraft]
    stance: str
    attributed_origin: str | None
    occurred_at: datetime | None
    occurred_precision: str
    confidence: float
    uncertainty_notes: list[str]
    injection_flags: list[str]
    extractor: str = EXTRACTOR_VERSION
    llm_used: bool = False
    raw_llm: dict | None = field(default=None, repr=False)

    def to_json(self) -> dict:
        d = asdict(self)
        d["occurred_at"] = self.occurred_at.isoformat() if self.occurred_at else None
        d.pop("raw_llm", None)
        return d


def _num(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUM.get(token)


def regex_form(text: str) -> str:
    """Cleaned text with Arabic orthography normalised and Arabic-Indic digits mapped to ASCII,
    punctuation preserved (needed for sentence-level regexes)."""
    return normalize_arabic(text)


def detect_wire(text: str) -> str | None:
    for key, pattern in _WIRES:
        if pattern.search(text):
            return key
    return None


def detect_stance(title: str, text: str, source_type: str | None) -> str:
    if _RETRACTION.search(title) or _RETRACTION.search(text[:400]):
        return "retraction"
    if _CORRECTION.search(title) or _CORRECTION.search(text[:200]):
        return "correction"
    if _DENIAL.search(regex_form(title)) or _DENIAL.search(regex_form(text)):
        return "denial"
    if source_type == "government":
        return "official_statement"
    return "report"


def extract_attribution(sentence: str) -> str | None:
    for pattern in _ATTR_EN:
        m = pattern.search(sentence)
        if m:
            src = m.group("src").strip(" ,.;:")
            if 2 < len(src) <= 80 and src.lower() not in {"it", "he", "she", "they", "this", "that", "the"}:
                return src
    for pattern in _ATTR_AR:
        m = pattern.search(sentence)
        if m:
            src = m.group("src").strip(" ،,.;:")
            if 2 < len(src) <= 80:
                return src
    return None


def extract_casualties(text: str) -> tuple[int | None, int | None, list[ClaimDraft], str | None]:
    killed: int | None = None
    injured: int | None = None
    claims: list[ClaimDraft] = []
    note = None
    for sentence in split_sentences(text):
        rf = regex_form(sentence)
        attributed = extract_attribution(sentence)
        official = bool(attributed and _OFFICIAL.search(regex_form(attributed)))
        if _ZERO_CAS_EN.search(sentence) or _ZERO_CAS_AR.search(rf):
            killed = killed if killed is not None else 0
            injured = injured if injured is not None else 0
            claims.append(ClaimDraft("casualties", clean_text(sentence, 300), attributed, official, "asserts", 0, "killed", 0.7))
            note = "Source explicitly reports no casualties."
            continue
        for pattern, num_group, kind_group in _CAS_EN:
            for m in pattern.finditer(sentence):
                groups = m.groups()
                if kind_group is None:
                    n, unit = _num(groups[0]), "killed"
                elif num_group == 1:
                    n, unit = _num(groups[0]), groups[1].lower()
                else:
                    unit, n = groups[0].lower(), _num(groups[1])
                if n is None:
                    continue
                unit = "killed" if unit in {"killed", "dead", "died", "killing", "kills"} else "injured"
                if unit == "killed":
                    killed = max(killed or 0, n)
                else:
                    injured = max(injured or 0, n)
                claims.append(ClaimDraft("casualties", clean_text(sentence, 300), attributed, official, "asserts", n, unit, 0.7))
        for pattern, unit, num_idx in _CAS_AR:
            for m in pattern.finditer(rf):
                n = int(m.group(num_idx))
                if unit == "killed":
                    killed = max(killed or 0, n)
                else:
                    injured = max(injured or 0, n)
                claims.append(ClaimDraft("casualties", clean_text(sentence, 300), attributed, official, "asserts", n, unit, 0.7))
    if claims and note is None:
        sources = {c.attributed_to for c in claims if c.attributed_to}
        note = ("Casualty figures as reported" + (f", attributed to {', '.join(sorted(sources))}" if sources else
                                                   ", without explicit attribution") + ".")
    return killed, injured, claims, note


def infer_occurrence(text: str, published_at: datetime | None, tz_name: str) -> tuple[datetime | None, str]:
    if published_at is None:
        return None, "unknown"
    tz = ZoneInfo(tz_name)
    local_pub = published_at.astimezone(tz)
    low = match_form(text)
    # explicit day + month
    m = re.search(r"\b(\d{1,2}) (" + "|".join(_MONTHS_EN) + r")\b", low) or \
        re.search(r"\b(" + "|".join(_MONTHS_EN) + r") (\d{1,2})\b", low)
    if m:
        a, b = m.group(1), m.group(2)
        day, month = (int(a), _MONTHS_EN[b]) if a.isdigit() else (int(b), _MONTHS_EN[a])
        try:
            cand = local_pub.replace(month=month, day=day, hour=12, minute=0, second=0, microsecond=0)
            if cand > local_pub + timedelta(days=1):
                cand = cand.replace(year=cand.year - 1)
            if timedelta(0) <= local_pub - cand <= timedelta(days=30) or abs(local_pub - cand) <= timedelta(days=1):
                return cand.astimezone(UTC), "day"
        except ValueError:
            pass
    for name, month in _MONTHS_AR.items():
        mm = re.search(r"(\d{1,2}) " + match_form(name), low)
        if mm:
            try:
                cand = local_pub.replace(month=month, day=int(mm.group(1)), hour=12, minute=0, second=0, microsecond=0)
                if timedelta(0) <= local_pub - cand <= timedelta(days=30) or abs(local_pub - cand) <= timedelta(days=1):
                    return cand.astimezone(UTC), "day"
            except ValueError:
                pass
    if re.search(r"\b(yesterday|last night|overnight)\b", low) or "امس" in low.split() or "الليله الماضيه" in low:
        cand = (local_pub - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        return cand.astimezone(UTC), "day"
    for word, wd in _WEEKDAYS.items():
        if re.search(r"\bon " + word + r"\b", low) or (not word.isascii() and match_form(word) in low.split()):
            delta = (local_pub.weekday() - wd) % 7
            cand = (local_pub - timedelta(days=delta)).replace(hour=12, minute=0, second=0, microsecond=0)
            return cand.astimezone(UTC), "day"
    if re.search(r"\b(earlier today|this morning|today)\b", low) or "اليوم" in low.split():
        return published_at, "day"
    return published_at, "reported"


def compute_relevance(location: ResolvedLocation, categories: list[str], gazetteer: Gazetteer) -> tuple[float, str]:
    if not categories:
        return 0.0, "no event category detected"
    affected = set(location.affected_countries)
    if location.country_code in GCC_CODES:
        return 1.0, f"primary location in GCC ({location.country_code})"
    if affected & GCC_CODES:
        return 0.8, "GCC country affected/mentioned: " + ",".join(sorted(affected & GCC_CODES))
    region = location.entity.region if location.entity else None
    if region in MARITIME_REGIONS:
        return 0.7, f"strategic waterway ({location.entity.name})"
    if region in CONFLICT_NEIGHBOURS and set(categories) & {"armed_clash", "political_violence"}:
        return 0.5, f"conflict in a neighbouring theatre ({region}) with direct GCC security linkage"
    if region and set(categories) & SECONDARY_GCC_RELEVANT:
        return 0.5, f"secondary region ({region}) with GCC-relevant event type"
    if region:
        return 0.3, f"secondary region ({region}); no GCC linkage detected"
    return 0.1, "no location resolved"


def rule_extract(*, title: str, excerpt: str, published_at: datetime | None, language: str,
                 source_type: str | None, gazetteer: Gazetteer, taxonomy: Taxonomy,
                 tz_name: str = "Asia/Riyadh") -> Extraction:
    title = clean_text(title)
    excerpt = clean_text(excerpt)
    full = f"{title}. {excerpt}" if excerpt else title
    flags = injection.flags(full)
    safe, removed = injection.strip_injected_sentences(full)
    notes: list[str] = []
    if removed:
        notes.append(f"Possible prompt injection: {removed} instruction-like sentence(s) ignored during extraction.")
    safe_title = title if not injection.scan(title) else ""

    classes = taxonomy.classify(safe)
    categories = [c.key for c in classes]
    scores = {c.key: c.score for c in classes}
    location = gazetteer.resolve(safe, safe_title)
    relevance, reason = compute_relevance(location, categories, gazetteer)
    killed, injured, cas_claims, cas_note = extract_casualties(safe)
    stance = detect_stance(safe_title, safe, source_type)
    wire = detect_wire(full)
    actors = [a.id for a in taxonomy.actors_in(safe)]
    sectors = taxonomy.sectors_for(safe)
    occurred, occ_precision = infer_occurrence(safe, published_at, tz_name)

    claims: list[ClaimDraft] = []
    first_sentence = split_sentences(safe)[0] if safe else title
    attributed = extract_attribution(safe)
    is_official = bool(attributed and _OFFICIAL.search(regex_form(attributed))) or source_type == "government"
    claim_type = {"denial": "denial", "correction": "correction", "retraction": "correction",
                  "official_statement": "official_statement"}.get(stance, "event_occurrence")
    claim_stance = {"denial": "denies", "correction": "corrects", "retraction": "retracts"}.get(stance, "asserts")
    if categories:
        claims.append(ClaimDraft(claim_type, clean_text(first_sentence, 300), attributed, is_official, claim_stance,
                                 confidence=min(0.9, 0.4 + 0.1 * max(scores.values()))))
    claims.extend(cas_claims)

    if location.notes:
        notes.extend(location.notes)
    if classes and max(scores.values()) < 2:
        notes.append("Event type inferred from a single weak keyword; classification confidence is low.")
    if occ_precision == "reported":
        notes.append("Event time not stated; using the publication time.")
    if wire:
        notes.append(f"Text attributes reporting to a wire service ({wire}); not counted as independent of that wire.")

    conf = 0.35
    if categories:
        conf += min(0.3, 0.06 * max(scores.values()))
    conf += 0.2 * location.confidence
    if flags:
        conf -= 0.15
    return Extraction(
        is_relevant=relevance >= 0.45 and bool(categories), relevance=relevance, relevance_reason=reason,
        categories=categories, category_scores=scores, primary_category=categories[0] if categories else None,
        title=title, title_en=title if language == "en" else None, summary_en=None,
        location={
            "entity_id": location.entity.id if location.entity else None,
            "name": location.entity.name if location.entity else None,
            "lat": location.lat, "lon": location.lon, "precision": location.precision,
            "confidence": location.confidence, "source": location.source, "country_code": location.country_code,
            "affected_countries": location.affected_countries, "mentioned": location.mentioned_entity_ids,
        },
        actors=actors, sectors=sectors, casualties_killed=killed, casualties_injured=injured, casualty_note=cas_note,
        claims=claims, stance=stance, attributed_origin=wire, occurred_at=occurred, occurred_precision=occ_precision,
        confidence=round(max(0.05, min(0.95, conf)), 2), uncertainty_notes=notes, injection_flags=flags,
    )


# ---------------------------------------------------------------------------
# LLM-assisted extraction
# ---------------------------------------------------------------------------
LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title_en", "summary_en", "event_types", "places", "organisations", "casualties", "attributions",
                 "uncertainty_notes"],
    "properties": {
        "title_en": {"type": "string", "description": "Faithful English rendering of the headline; keep proper names."},
        "summary_en": {"type": "string", "description": "One or two neutral sentences describing only what the text reports."},
        "event_types": {"type": "array", "items": {"type": "string"}},
        "places": {"type": "array", "items": {"type": "string"}, "description": "Place names exactly as written in the text."},
        "organisations": {"type": "array", "items": {"type": "string"}},
        "casualties": {
            "type": "object", "additionalProperties": False, "required": ["killed", "injured", "quote"],
            "properties": {"killed": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                           "injured": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                           "quote": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        },
        "attributions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["claim", "attributed_to", "stance"],
            "properties": {"claim": {"type": "string"},
                           "attributed_to": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                           "stance": {"type": "string", "enum": ["asserts", "denies", "disputes", "corrects", "retracts"]}}}},
        "uncertainty_notes": {"type": "array", "items": {"type": "string"}},
    },
}

LLM_SYSTEM = """You extract structured facts from one news item for a regional security monitoring desk.
Rules:
- Report only what the text states. Do not add background knowledge, dates, numbers, places or actors that are not in the text.
- Keep proper names as written; transliterate Arabic/Persian names conservatively and do not guess spellings.
- Casualty numbers only if explicitly stated; quote the exact phrase in `quote`. Otherwise use null.
- Official statements are claims attributed to their author, not established facts.
- Organisations only - never list private individuals.
- event_types must be chosen from the allowed list provided; use [] if none apply.
""" + "\n" + injection.UNTRUSTED_DATA_POLICY


def _appears(value: str | None, text: str) -> bool:
    if not value:
        return False
    v = match_form(value)
    return bool(v) and v in match_form(text)


def llm_enrich(extraction: Extraction, *, title: str, excerpt: str, language: str, llm: LLMService,
               gazetteer: Gazetteer, taxonomy: Taxonomy) -> Extraction:
    """Validate-and-merge LLM output into a rule-based extraction. Never raises on LLM failure."""
    if not llm.available:
        return extraction
    source_text = f"{title}\n{excerpt}"
    # Defence in depth: the model never sees sentences flagged as instructions.
    source_text, removed = injection.strip_injected_sentences(source_text)
    allowed = sorted(taxonomy.event_types)
    user = (f"Allowed event_types: {', '.join(allowed)}\n\n"
            + injection.fence("article", source_text, {"language": language}))
    try:
        result = llm.json(agent="extraction", task="extraction", system=LLM_SYSTEM, user=user,
                          schema=LLM_SCHEMA, schema_name="news_item_extraction", max_tokens=3000, effort="low")
    except LLMUnavailable:
        return extraction
    except LLMError as exc:
        extraction.uncertainty_notes.append(f"LLM extraction unavailable for this item ({type(exc).__name__}); rules only.")
        return extraction
    data = result.data or {}
    extraction.raw_llm = data
    extraction.llm_used = True
    extraction.extractor = f"{EXTRACTOR_VERSION}+llm:{result.model}"
    rejected: list[str] = []

    title_en = (data.get("title_en") or "").strip()
    if title_en and language != "en":
        extraction.title_en = clean_text(title_en, 300)
    summary = (data.get("summary_en") or "").strip()
    if summary:
        extraction.summary_en = clean_text(summary, 600)

    llm_types = [t for t in data.get("event_types", []) if t in taxonomy.event_types]
    rejected += [t for t in data.get("event_types", []) if t not in taxonomy.event_types]
    # A model-proposed category is accepted only when at least one of its keywords occurs in the
    # (injection-stripped) text; otherwise a manipulated or hallucinating model could inflate severity.
    supported = {c.key for c in taxonomy.classify(source_text, min_score=1.0)}
    for t in llm_types:
        if t in extraction.categories:
            continue
        if t in supported:
            extraction.categories.append(t)
            extraction.category_scores[t] = 1.5
        else:
            rejected.append(f"event_type:{t} (no supporting keyword in text)")
    if extraction.categories and not extraction.primary_category:
        extraction.primary_category = extraction.categories[0]

    if not extraction.location.get("entity_id"):
        for place in data.get("places", []):
            if not _appears(place, source_text):
                rejected.append(f"place:{place}")
                continue
            resolved = gazetteer.resolve(place, place)
            if resolved.entity:
                extraction.location.update({
                    "entity_id": resolved.entity.id, "name": resolved.entity.name, "lat": resolved.lat,
                    "lon": resolved.lon, "precision": resolved.precision,
                    "confidence": round(max(0.1, resolved.confidence - 0.1), 2), "source": resolved.source,
                    "country_code": resolved.country_code,
                    "affected_countries": sorted(set(extraction.location.get("affected_countries") or [])
                                                 | set(resolved.affected_countries)),
                })
                extraction.uncertainty_notes.append(f"Location '{place}' identified with model assistance and resolved via gazetteer.")
                break

    cas = data.get("casualties") or {}
    quote = cas.get("quote")
    for key, attr in (("killed", "casualties_killed"), ("injured", "casualties_injured")):
        val = cas.get(key)
        if isinstance(val, int) and val >= 0:
            if quote and _appears(quote, source_text) and (str(val) in regex_form(source_text) or val == 0):
                if getattr(extraction, attr) is None:
                    setattr(extraction, attr, val)
            else:
                rejected.append(f"casualties.{key}={val} (not found verbatim in source)")

    for att in data.get("attributions", []):
        who = att.get("attributed_to")
        if who and not _appears(who, source_text):
            rejected.append(f"attribution:{who}")
            who = None
        claim_text = clean_text(att.get("claim") or "", 300)
        if claim_text:
            extraction.claims.append(ClaimDraft(
                "denial" if att.get("stance") == "denies" else "event_occurrence", claim_text, who,
                bool(who and _OFFICIAL.search(regex_form(who))), att.get("stance", "asserts"), confidence=0.55))

    for note in data.get("uncertainty_notes", [])[:5]:
        extraction.uncertainty_notes.append("Model note: " + clean_text(note, 240))
    if rejected:
        extraction.uncertainty_notes.append("Discarded unsupported model output: " + "; ".join(rejected[:6]))
    relevance, reason = compute_relevance(
        ResolvedLocation(gazetteer.entities.get(extraction.location.get("entity_id") or ""), extraction.location.get("lat"),
                         extraction.location.get("lon"), extraction.location.get("precision") or "unknown",
                         extraction.location.get("confidence") or 0, extraction.location.get("source"),
                         extraction.location.get("country_code"), extraction.location.get("affected_countries") or [],
                         [], []),
        extraction.categories, gazetteer)
    extraction.relevance, extraction.relevance_reason = relevance, reason
    extraction.is_relevant = relevance >= 0.45 and bool(extraction.categories)
    return extraction
