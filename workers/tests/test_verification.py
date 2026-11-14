"""Independent corroboration: origins, not article counts."""

from gccintel.agents.verification import Evidence, assess

_counter = iter(range(1, 10_000))


def ev(origin, role="original", stance="report", rel="B", stype="news_media"):
    return Evidence(str(next(_counter)), origin, role, stance, rel, stype, origin)


def test_three_copies_of_one_wire_are_one_origin():
    r = assess([ev("origin:reuters"), ev("origin:reuters", "syndicated"), ev("origin:reuters", "syndicated")])
    assert r.status == "single_source"
    assert r.independent_count == 1 and r.source_count == 3
    assert any("do not count as corroboration" in n for n in r.notes)


def test_two_independent_reliable_origins_corroborate():
    r = assess([ev("origin:reuters"), ev("origin:qna", "follow_up")])
    assert r.status == "corroborated" and r.credibility == 2


def test_low_reliability_only_stays_unverified():
    r = assess([ev("origin:social-a", rel="E"), ev("origin:social-b", rel="E")])
    assert r.status == "unverified" and r.credibility == 6


def test_denial_makes_disputed_and_keeps_both():
    r = assess([ev("origin:reuters"), ev("origin:gov", "contradicting", "denial", stype="government")])
    assert r.status == "disputed"
    assert r.denying_origins == ["origin:gov"]
    assert "Contradictory reporting." in r.needs_review_reasons


def test_retraction_of_only_origin_unverifies():
    r = assess([ev("origin:a"), ev("origin:a", "retraction", "retraction")])
    assert r.status == "unverified"
    assert "Original reporting retracted." in r.needs_review_reasons


def test_confirmed_requires_analyst_unless_policy_allows():
    evs = [ev("origin:a"), ev("origin:b"), ev("origin:c")]
    assert assess(evs).status == "corroborated"
    assert assess(evs, analyst_confirmed=True).status == "confirmed"
    assert assess(evs, auto_confirm=True).status == "confirmed"


def test_official_statement_is_attributable_claim():
    r = assess([ev("origin:gov", "official_statement", "official_statement", stype="government")])
    assert r.status == "single_source"
    assert any("attributable claims" in n for n in r.notes)
