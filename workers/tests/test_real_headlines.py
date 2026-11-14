"""Regression cases from live public feeds (14 November 2026).

Each headline below was mishandled by an earlier version of the rule-based analysis when the platform first
ran against real sources: a dateline read as the event location, a reaction or a running tally turned into an
incident, a place that was only a topic chosen as the location, an event abroad placed in the region.
The texts are short headlines and first sentences, kept to what each case needs.
"""

from datetime import UTC, datetime

import pytest

from gccintel.agents.extraction import rule_extract
from gccintel.refdata import default_gazetteer, default_taxonomy
from gccintel.text import events
from gccintel.text.normalize import clean_text, match_form

GAZ = default_gazetteer()
TAX = default_taxonomy()


def extract(title: str, excerpt: str = "", language: str = "en"):
    return rule_extract(title=title, excerpt=excerpt, published_at=datetime.now(UTC), language=language,
                        source_type="news_media", gazetteer=GAZ, taxonomy=TAX)


def test_agency_dateline_is_where_the_story_was_filed_not_the_event():
    text, place = events.strip_dateline("Riyadh, November 09 (QNA) - The Coalition said it intercepted a drone.")
    assert place == "Riyadh" and text.startswith("The Coalition")
    text, place = events.strip_dateline("الرياض - واس - أعلن التحالف اعتراض طائرة مسيرة")
    assert place == "الرياض" and text.startswith("أعلن")
    ex = extract("Coalition intercepts drone launched toward Jazan",
                 "Riyadh, November 09 (QNA) - The Coalition intercepted a drone launched toward Jazan.")
    assert ex.location["entity_id"] == "city:sa:jazan"
    assert any("Dateline" in n for n in ex.uncertainty_notes)


@pytest.mark.parametrize("title,excerpt", [
    # A reaction to attacks is not a new attack.
    ("Kuwaiti Cabinet Condemns Continued Houthi Attacks on Saudi Arabia",
     "The cabinet condemned the continued Houthi attacks on Saudi Arabia."),
    # Running tallies sum up earlier events.
    ("UN says 25 seafarers killed in, around Hormuz since US-Israel war on Iran",
     "The UN said 25 seafarers had been killed since the start of the war."),
    # Interviews and analysis are not event reports.
    ("QNA Interview: Lebanon Min. Praises Qatar's Mediation Efforts to Ease Regional Tensions",
     "The minister praised Qatar's mediation efforts."),
    # A feature story that mentions fighting only as background.
    ("Newborn twins fight for survival as conflict fuels Yemen's hunger crisis",
     "Raghad and Raad's family had to flee their home on foot because of the fighting between pro-government "
     "forces and the Houthis."),
    # "مسيرة" is also a career: Arabic sport news is not a drone report.
    ("مسيرة محمد صلاح مع ليفربول", "يواصل محمد صلاح مسيرته مع ليفربول."),
])
def test_items_that_are_not_new_events_do_not_become_incidents(title, excerpt):
    ex = extract(title, excerpt, "ar" if title[0] > "؀" else "en")
    assert not ex.is_relevant, (ex.categories, ex.relevance_reason)


def test_cumulative_war_toll_is_a_tally_but_a_single_incident_update_is_not():
    assert events.is_tally("Gaza Death Toll From Occupation's Offensive Rises to 74,645: Health Officials")
    assert not events.is_tally("Death toll from Riyadh warehouse blast rises to 12")


def test_event_outside_the_coverage_area_is_not_placed_in_the_region():
    ex = extract("US responds to suspected drone activity over UK air base used in Iran strikes",
                 "US Air Force personnel at the RAF Lakenheath base in Britain responded to suspected unauthorised "
                 "drone activity on Friday evening.")
    assert not ex.is_relevant
    assert ex.location["name"] == "United Kingdom" and ex.location["lat"] is None
    assert "outside the coverage area" in ex.relevance_reason


def test_demand_for_sanctions_is_not_a_sanctions_event():
    ex = extract("Protesters urge UK government to impose sanctions on Israel over Gaza genocide",
                 "Demonstrators demand 'meaningful action' and an inquiry.")
    assert ex.categories == ["protest"]
    assert not ex.is_relevant


@pytest.mark.parametrize("title,excerpt,entity", [
    # A capital that acts is not where the event happened; the place after "in" is.
    ("Iran seizes tanker", "The IRGC seized a tanker in the Strait of Hormuz on Friday.", "maritime:strait-of-hormuz"),
    ("«الحرس الثوري» يعلن اصطدام ناقلتي نفط بألغام في مضيق هرمز", "", "maritime:strait-of-hormuz"),
    # A place in the headline beats one in the text, but a more precise place inside it is used.
    ("Explosion reported in Saudi Arabia", "An explosion was reported at the port in Jeddah.", "city:sa:jeddah"),
    # "on" introducing a topic ("positions on Gaza") does not place the event.
    ("Türkiye, Qatar reaffirm defense ties, call for diplomacy on Iran",
     "Joint communique outlines mutual defense agreement and common positions on Gaza, Syria and regional security",
     "country:qa"),
    # A place that names an agreement ("Makkah pact") is not a location.
    ("Turkey's approval of Makkah pact takes effect as it signs separate defence agreement with Qatar",
     "Turkey's law approving the Makkah Defence Pact took effect on Saturday.", "country:qa"),
    # A country adjective ("Israeli bombardment") and an origin ("drone from Ethiopia") are not the location.
    ("Three Palestinians Killed, Others Wounded in Israeli Bombardment of Gaza Strip", "", "city:ps:gaza"),
    ("Sudanese army advances in West Kordofan, says it downed drone from Ethiopia", "", "country:sd"),
    # "on" after an attack word does place it.
    ("Israeli strike on Gaza hospital kills six Palestinians, health ministry says", "", None),
])
def test_location_is_where_the_event_happened(title, excerpt, entity):
    ex = extract(title, excerpt, "ar" if title[0] > "؀" else "en")
    if entity is None:
        assert ex.location["basis"] == "title-locative" and ex.location["country_code"] == "PS"
    else:
        assert ex.location["entity_id"] == entity, ex.location


def test_possessive_does_not_hide_a_place():
    assert match_form("UAE's ADNOC") == "uae adnoc"
    ex = extract("UAE's ADNOC Logistics & Services: Vessel Sustained Damage Following Attack")
    assert ex.location["country_code"] == "AE"
    assert "maritime_incident" in ex.categories


def test_mojibake_arabic_is_repaired_before_normalisation():
    broken = "الرياض".encode().decode("cp1252")
    assert broken != "الرياض"
    assert clean_text(broken) == "الرياض"


def test_ceasefire_violation_is_a_clash_not_a_diplomatic_development():
    ex = extract("Israeli attacks kill 6 Palestinians, injure around 20 in Gaza despite ceasefire")
    assert ex.primary_category == "armed_clash"
    assert "geopolitical_development" not in ex.categories
    ex = extract("Saudi Arabia and Iran agree to a ceasefire agreement in Yemen talks")
    assert ex.primary_category == "geopolitical_development"
