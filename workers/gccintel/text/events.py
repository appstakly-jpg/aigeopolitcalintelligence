"""Does a text report a specific event? Cues for rule-based extraction (English and Arabic).

Event-type keywords say what a text is about; these cues say whether it reports something that
happened. On live feeds (14 November 2026) many items carried incident keywords without reporting a
new incident:

* commentary and capability news: "Houthi threat and Iran war reshape Saudi Arabia's security
  priorities", "Iraq expects first purchases of air defence systems to arrive soon";
* reactions: "Kuwaiti Cabinet Condemns Continued Houthi Attacks on Saudi Arabia";
* running tallies: "UN says 25 seafarers killed in, around Hormuz since US-Israel war on Iran";
* Arabic sport: "مسيرة محمد صلاح" (Salah's career; مسيرة also means "drone");
* datelines read as places: "Riyadh, November 09 (QNA) - The Coalition ... in Yemen ...".

Everything here is deterministic and conservative: an item that is dropped as an incident is still
stored, searchable and available to the AI analyst.
"""

from __future__ import annotations

import re

from .normalize import normalize_arabic

# Categories that describe violence or physical damage: they need a reported event to become an incident.
KINETIC = frozenset({"armed_clash", "drone_missile", "political_violence", "terrorism", "cross_border_attack",
                     "maritime_incident", "energy_disruption"})

_CUES_EN = [
    r"attack(?:s|ed|ing)?", r"strikes?", r"struck", r"hits?", r"hitting", r"target(?:s|ed|ing)", r"launch(?:es|ed|ing)",
    r"fired", r"firing", r"intercept(?:s|ed|ing|ions?)?", r"shot down", r"shoots? down", r"down(?:s|ed)",
    r"destroy(?:s|ed|ing)?", r"explo(?:des?|ded|ding|sions?)", r"blasts?", r"detonat\w*", r"kill(?:s|ed|ing)?",
    r"dead", r"deaths?", r"wound(?:s|ed)", r"injur(?:e|es|ed|ing|ies|y)", r"casualt(?:y|ies)", r"seiz(?:e|es|ed|ing|ure)",
    r"captur(?:e|es|ed|ing)", r"recaptur\w*", r"retak(?:e|es|en|ing)", r"hijack\w*", r"clash(?:es|ed)?", r"fighting",
    r"fought", r"raids?", r"raided", r"bomb(?:s|ed|ing|ings)?", r"shell(?:s|ed|ing)", r"ambush\w*", r"assassinat\w*",
    r"abduct\w*", r"kidnap\w*", r"board(?:ed|ing)", r"sank", r"sunk", r"sink(?:s|ing)?", r"damag(?:e|ed|es|ing)", r"caught fire",
    r"ablaze", r"on fire", r"fires?", r"collid\w*", r"crash(?:es|ed)?", r"mines?", r"offensive", r"advanc(?:e|es|ed|ing)",
    r"incidents?", r"halt(?:s|ed)", r"shut(?:s|down)?", r"outages?",
]
_CUE_EN = re.compile(r"\b(?:" + "|".join(_CUES_EN) + r")\b", re.IGNORECASE)
# Arabic stems in normalised spelling (أ/إ/آ -> ا, ة -> ه, ى -> ي); clitics may precede them.
_CUES_AR = [
    "هجوم", "هجمات", "هاجم", "استهدف", "تستهدف", "يستهدف", "قصف", "غاره", "غارات", "اسقاط", "اسقط", "اعتراض",
    "اعترض", "انفجار", "تفجير", "حريق", "حرائق", "اندلاع", "مقتل", "قتلى", "قتيل", "قتل", "اصابه", "اصابات",
    "جرحى", "جريح", "احتجاز", "اختطاف", "اشتباك", "معارك", "سيطره", "تدمير", "دمر", "اصطدام", "اصطدمت", "غرق",
    "تضرر", "تحطم", "شن", "اطلاق", "اطلق", "ضربه", "ضربات", "اعتقال", "توغل", "اقتحام", "اقتحم", "استعاد", "ضبط",
    "لغم", "الغام", "بالغام", "انقطاع",
]
_CUE_AR = re.compile(r"(?:^|[\s\"'«(])(?:و|ف|ب|ل|لل|ال|وال|بال)?(?:" + "|".join(_CUES_AR) + ")")

_REACTION_EN = re.compile(
    r"\b(?:condemn(?:s|ed)?|denounce[sd]?|deplore[sd]?|welcome[sd]?|demand(?:s|ed)?|urge[sd]?|"
    r"call(?:s|ed)? (?:for|on)|express(?:es|ed)? (?:concern|solidarity|condolences)|slam(?:s|med)?|praise[sd]?|"
    r"warn(?:s|ed)? (?:of|against|that))\b", re.IGNORECASE)
_REACTION_AR = re.compile(r"(?:^|\s)(?:و)?(?:يدين|تدين|ادان|ادانت|يستنكر|تستنكر|استنكر|استنكرت|يرحب|ترحب|رحب|رحبت|"
                          r"يطالب|تطالب|طالب|طالبت|يشجب|تشجب|شجب|شجبت|يحذر|تحذر|حذر|حذرت)(?:\s|$)")

_TALLY_EN = re.compile(
    r"\b(?:since (?:the )?(?:start|beginning|outbreak) of|since (?:the )?(?:[\w-]+ ){0,2}war\b|so far (?:this|in)\b|"
    r"(?:in|over|during) (?:a|one|the (?:past|last)) (?:week|month|year)\b|over the (?:past|last) \d+ (?:days|weeks|months)|"
    # "Gaza death toll rises to 74,645": a cumulative toll of a war, not one incident (a single incident's
    # updated toll stays below a thousand).
    r"\btoll\b[^.]{0,60}?\b(?:rises|rose|climbs|climbed|reaches|reached|hits|tops|surpasses|exceeds|passes|jumps)\s+"
    r"(?:to\s+)?(?:over\s+|more than\s+|nearly\s+|almost\s+)?(?:\d{1,3}(?:,\d{3})+|\d{4,}))",
    re.IGNORECASE)
_TALLY_AR = re.compile(r"(?:منذ (?:بدء|بدايه|اندلاع|مطلع)|حصيله|خلال (?:اسبوع|الاسبوع|شهر|الشهر|عام|العام))")

_COMMENTARY_EN = re.compile(
    r"^(?:analysis|opinion|comment|explainer|editorial|podcast|video|watch|in pictures)\b|\?\s*['\"’]?\s*$|"
    r"^(?:why|how|what|who|will|can|could|should|is|are|does|do)\b|\binterview\b", re.IGNORECASE)

# "Riyadh, November 09 (QNA) - ", "DOHA (Reuters) - ", "Gaza, Nov 9 (AFP) –"
_DATELINE_EN = re.compile(
    r"^\s*(?P<place>[A-Z][\w.'’\- ]{1,40}?)(?:,\s*[A-Z][\w.'’\- ]{1,30}?)?"
    r"(?:,\s*(?:[A-Z][a-z]{2,9}\.?\s+\d{1,2}|\d{1,2}\s+[A-Z][a-z]{2,9}))?"
    r"\s*\((?P<agency>[A-Za-z][A-Za-z .]{1,24})\)\s*[-–—:]+\s*")
# "الرياض - واس - ", "الدوحة - قنا: "
_DATELINE_AR = re.compile(r"^\s*(?P<place>[؀-ۿ ]{2,30}?)\s*[-–—:]\s*[«\"(]?(?P<agency>واس|قنا|وام|كونا|بنا|العمانية|"
                          r"العمانيه|سبأ|سبا|رويترز|أ ف ب|ا ف ب|الأناضول|الاناضول)[»\")]?\s*[-–—:]\s*")


def strip_dateline(text: str) -> tuple[str, str | None]:
    """Remove a news-agency dateline from the start of a text. The dateline names where the story was
    filed, not where the event happened, so it must not be read as the event's location."""
    if not text:
        return text, None
    for pattern in (_DATELINE_EN, _DATELINE_AR):
        m = pattern.match(text)
        if m:
            return text[m.end():].lstrip(), m.group("place").strip()
    return text, None


def event_cue(text: str) -> str | None:
    """The first word or phrase that reports something happening, if any."""
    m = _CUE_EN.search(text or "")
    if m:
        return m.group(0)
    m = _CUE_AR.search(normalize_arabic(text or ""))
    return m.group(0).strip(" \"'«(") if m else None


def is_reaction(title: str) -> bool:
    """Headline reports a reaction (condemns, welcomes, demands ...), not the event itself."""
    return bool(_REACTION_EN.search(title or "") or _REACTION_AR.search(normalize_arabic(title or "")))


def is_tally(title: str) -> bool:
    """Headline sums up earlier events ("since the war began", "in a week")."""
    return bool(_TALLY_EN.search(title or "") or _TALLY_AR.search(normalize_arabic(title or "")))


def is_commentary(title: str) -> bool:
    """Analysis, opinion, interviews, explainers and question headlines."""
    return bool(_COMMENTARY_EN.search((title or "").strip()))
