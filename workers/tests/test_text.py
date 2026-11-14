"""Multilingual parsing, normalisation, geolocation, classification and extraction (no DB)."""

from datetime import UTC, datetime

import pytest

from gccintel.agents.extraction import detect_stance, extract_casualties, infer_occurrence, rule_extract
from gccintel.refdata import default_gazetteer, default_taxonomy
from gccintel.text.lang import detect_language
from gccintel.text.normalize import match_form, normalize_arabic

GAZ = default_gazetteer()
TAX = default_taxonomy()


@pytest.mark.parametrize("text,expected", [
    ("Explosion reported near the port of Jeddah, officials said on Monday", "en"),
    ("أفادت مصادر بوقوع انفجار في ميناء جدة اليوم", "ar"),
    ("گزارش ها حاکی از آن است که این حمله در نزدیکی بندر انجام شده است", "fa"),
])
def test_language_detection(text, expected):
    assert detect_language(text)[0] == expected


@pytest.mark.parametrize("text,hint,expected", [
    # Real headlines from English feeds that were misdetected: "UN" read as the French "un",
    # and "Türkiye" outweighing the English words.
    ("UN says 25 seafarers killed in, around Hormuz since US-Israel war on Iran 'Situation cannot become new normal', says UN's envoy",
     "en", "en"),
    ("Türkiye, Qatar sign 5 agreements, including mutual defense pact", "en", "en"),
    # Clear evidence still overrides the feed's declared language.
    ("Le président est arrivé dans la capitale pour une visite de deux jours et les discussions", "en", "fr"),
])
def test_language_detection_uses_feed_language_and_ignores_acronyms(text, hint, expected):
    assert detect_language(text, hint)[0] == expected


def test_arabic_normalisation_unifies_orthography_and_digits():
    assert normalize_arabic("إصابة") == normalize_arabic("اصابه")
    assert normalize_arabic("مُسَيَّرة") == normalize_arabic("مسيرة")
    assert "12" in match_form("إصابة ١٢ شخصا")


def test_arabic_place_and_event_type():
    ex = rule_extract(title="اعتراض طائرة مسيرة في أجواء جازان", excerpt="أعلنت الدفاعات الجوية اعتراض طائرة مسيرة.",
                      published_at=datetime.now(UTC), language="ar", source_type="news_media",
                      gazetteer=GAZ, taxonomy=TAX)
    assert ex.location["entity_id"] == "city:sa:jazan"
    assert "drone_missile" in ex.categories
    assert ex.is_relevant


def test_ambiguous_oman_amman_resolved_from_context():
    # "عمان" is both Oman and Amman; Jordanian context must win.
    r = GAZ.resolve("وصل الوفد إلى عمان عاصمة الأردن لإجراء محادثات", "")
    assert r.entity is not None
    assert r.country_code == "JO" or r.entity.id.startswith("city:jo")


def test_longest_place_match_wins():
    r = GAZ.resolve("A cargo ship was seized in the Gulf of Oman", "")
    assert r.entity.id == "maritime:gulf-of-oman"
    assert "OM" not in r.affected_countries


def test_no_coordinates_invented_without_gazetteer_match():
    ex = rule_extract(title="Explosion reported at a warehouse", excerpt="Officials said an explosion occurred.",
                      published_at=datetime.now(UTC), language="en", source_type="news_media",
                      gazetteer=GAZ, taxonomy=TAX)
    assert ex.location["lat"] is None and ex.location["lon"] is None
    assert ex.location["precision"] == "unknown"
    assert not ex.is_relevant


def test_source_reported_coordinates_are_points_with_provenance():
    r = GAZ.resolve("Vessel reported an incident at 12.58N, 43.05E west of the Bab el-Mandeb", "")
    assert r.precision == "point" and r.source == "source-reported"
    assert abs(r.lat - 12.58) < 1e-6 and abs(r.lon - 43.05) < 1e-6


@pytest.mark.parametrize("text,killed,injured", [
    ("At least three people were killed and 12 injured in the blast", 3, 12),
    ("The attack killed two soldiers", 2, None),
    ("Debris from an intercepted missile injured two people", None, 2),
    ("مقتل 4 أشخاص وإصابة 9 آخرين في الهجوم", 4, 9),
    ("No casualties were reported.", 0, 0),
    ("دون وقوع إصابات", 0, 0),
])
def test_casualty_extraction(text, killed, injured):
    k, i, _, _ = extract_casualties(text)
    assert (k, i) == (killed, injured)


def test_stance_detection():
    assert detect_stance("Ministry denies reports of attack", "The ministry denied the reports.", "government") == "denial"
    assert detect_stance("CORRECTION: outage was in Ahmadi", "An earlier version gave an incorrect location.", "news_media") == "correction"
    assert detect_stance("Outlet retracts story on refinery fire", "The outlet withdrew the report.", "news_media") == "retraction"


def test_occurrence_time_uses_source_local_date():
    # Published 01:00 Riyadh time on 10 Nov (= 22:00 UTC 9 Nov): "yesterday" means 9 Nov local, not 8 Nov.
    published = datetime(2026, 11, 9, 22, 0, tzinfo=UTC)
    occurred, precision = infer_occurrence("The attack happened yesterday evening.", published, "Asia/Riyadh")
    assert precision == "day"
    assert occurred.astimezone().date().isoformat() >= "2026-11-08"
    from zoneinfo import ZoneInfo
    assert occurred.astimezone(ZoneInfo("Asia/Riyadh")).date().isoformat() == "2026-11-09"
