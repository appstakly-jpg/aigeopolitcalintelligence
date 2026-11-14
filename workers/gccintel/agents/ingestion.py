"""Ingestion Agent (B).

Fetches due sources politely (robots.txt, throttling, conditional GET), normalises and
canonicalises items, removes exact duplicates, detects near-duplicate syndication with
SimHash + shingle Jaccard, detects language, flags prompt-injection text and stores only the
excerpt each source's usage policy permits. Every fetch writes a source_health row.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, datetime, timedelta

import psycopg

from ..adapters.base import AdapterResult, RawItem
from ..adapters.gdelt import GdeltAdapter
from ..adapters.http import PoliteFetcher
from ..adapters.rss import RssAdapter
from ..db import execute, fetch_all, fetch_one, jsonb
from ..log import get_logger
from ..text import injection
from ..text.lang import detect_language
from ..text.normalize import clean_text, match_form
from ..text.simhash import from_signed, hamming, jaccard, simhash, to_signed
from ..text.urls import canonicalize_url, url_hash
from .extraction import detect_wire

log = get_logger(__name__)

AGENT = "ingestion-agent"
NEAR_DUP_HAMMING = 6
NEAR_DUP_JACCARD = 0.6
NEAR_DUP_TITLE_JACCARD = 0.5        # headline word-pair overlap for a copy
NEAR_DUP_MIN_EXCERPT_WORDS = 25     # or: excerpts this long ...
NEAR_DUP_EXCERPT_JACCARD = 0.8      # ... reproduced almost word for word under a new headline
MAX_ITEMS_PER_FETCH = 100
FAILURE_BACKOFF_MINUTES = [5, 15, 60, 180, 720]


def due_sources(conn: psycopg.Connection, tenant_id: str, *, force: bool = False, source_ids: list[str] | None = None,
                limit: int = 50) -> list[dict]:
    return fetch_all(conn, """
        select * from sources
        where tenant_id = %s and enabled and status = 'approved'
          and (%s or next_fetch_at is null or next_fetch_at <= now())
          and (%s::uuid[] is null or id = any(%s::uuid[]))
        order by priority, next_fetch_at nulls first limit %s""",
                     (tenant_id, force, source_ids, source_ids, limit))


def adapter_for(source: dict, fetcher: PoliteFetcher):
    if source["adapter"] == "rss":
        return RssAdapter(fetcher)
    if source["adapter"] == "gdelt":
        return GdeltAdapter(fetcher)
    return None


def excerpt_for(source: dict, text: str) -> str | None:
    """Store only what the usage policy permits; never more than max_excerpt_chars."""
    if source["usage_policy"] == "metadata_only" or not text:
        return None
    return clean_text(text, int(source["max_excerpt_chars"])) or None


_NAME_RE = re.compile(r"[^\W\d_][\w'\u2019-]*|\d+", re.UNICODE)


def _names_substituted(title: str, other_title: str) -> bool:
    """True when each headline has capitalised words or numbers the other lacks, i.e. a name or
    figure was swapped rather than words added ("Mongolia - Level 1: ..." / "Samoa - Level 1: ...")."""
    def names(t: str) -> set[str]:
        return {w.lower() for w in _NAME_RE.findall(t) if w[:1].isupper() or w.isdigit()}
    a, b = names(title), names(other_title)
    return bool(a - b) and bool(b - a)


def _is_copy(title: str, summary: str, other_title: str, other_summary: str, sim: float, distance: int,
             same_origin: bool = False) -> bool:
    """A near copy keeps its headline (lightly edited at most) or reproduces a long excerpt almost
    word for word. Short, templated items with different headlines are separate items even when
    their text is nearly identical, e.g. one government's travel advice for several countries
    ("Bahrain travel advice" / "Kuwait travel advice" with the same change note). Within one
    publisher, headlines that swap a name or figure are separate items too; across publishers that
    case still counts as a copy, which is the cautious choice for corroboration."""
    if not ((distance <= NEAR_DUP_HAMMING and sim >= 0.35) or sim >= NEAR_DUP_JACCARD):
        return False
    if same_origin and _names_substituted(title, other_title):
        return False
    if jaccard(title, other_title) >= NEAR_DUP_TITLE_JACCARD:
        return True
    long_enough = min(len(summary.split()), len(other_summary.split())) >= NEAR_DUP_MIN_EXCERPT_WORDS
    return long_enough and jaccard(summary, other_summary) >= NEAR_DUP_EXCERPT_JACCARD


def _near_duplicate(conn: psycopg.Connection, tenant_id: str, sh: int, text: str, content_hash: str,
                    exclude_source: str, title: str = "", summary: str = "",
                    own_origin: str | None = None) -> tuple[dict | None, float]:
    """Earliest recent article that this item copies (same content hash, or SimHash+Jaccard near copy)."""
    exact = fetch_one(conn, """
        select id, source_id, meta, attributed_origin, published_at from articles
        where tenant_id = %s and content_hash = %s order by coalesce(published_at, retrieved_at) limit 1""",
                      (tenant_id, content_hash))
    if exact:
        return exact, 1.0
    rows = fetch_all(conn, """
        select id, source_id, meta, attributed_origin, simhash, title, excerpt, published_at, retrieved_at
        from articles where tenant_id = %s and simhash is not null and retrieved_at > now() - interval '7 days'
        order by retrieved_at desc limit 3000""", (tenant_id,))
    best, best_sim = None, 0.0
    for r in rows:
        d = hamming(sh, from_signed(r["simhash"]))
        if d > 16:
            continue
        sim = jaccard(text, f"{r['title']} {r['excerpt'] or ''}")
        same_origin = own_origin is not None and (r["meta"] or {}).get("origin_key") == own_origin
        if _is_copy(title or text, summary, r["title"], r["excerpt"] or "", sim, d, same_origin):
            # prefer the earliest copy as the parent
            if best is None or sim > best_sim + 0.1 or (
                    abs(sim - best_sim) <= 0.1 and (r["published_at"] or r["retrieved_at"]) < (best["published_at"] or best["retrieved_at"])):
                best, best_sim = r, sim
    return best, best_sim


def origin_key(source: dict, item: RawItem, wire: str | None, parent: dict | None) -> str:
    if parent and (parent.get("meta") or {}).get("origin_key"):
        return parent["meta"]["origin_key"]
    # Wires and source origin groups share one namespace ("origin:reuters") so a wire's own feed
    # and other outlets' copies of that wire collapse to the same independent origin.
    if wire:
        return f"origin:{wire}"
    if source["adapter"] == "gdelt" and item.publisher_domain:
        return f"domain:{item.publisher_domain}"
    return f"origin:{source.get('origin_group') or source['id']}"


def store_item(conn: psycopg.Connection, source: dict, item: RawItem) -> str | None:
    """Insert one item as an article; returns its id, or None for an exact duplicate URL."""
    if not item.url or not item.title:
        return None
    canonical = canonicalize_url(item.url)
    h = url_hash(canonical)
    if fetch_one(conn, "select 1 from articles where tenant_id = %s and url_hash = %s", (source["tenant_id"], h)):
        return None
    title = clean_text(item.title, 500)
    summary = clean_text(item.summary)
    excerpt_note = None
    stored = excerpt_for(source, summary)
    if stored and fetch_one(conn, """select 1 from articles where tenant_id = %s and source_id = %s and excerpt = %s
                                     and title <> %s limit 1""", (source["tenant_id"], source["id"], stored, title)):
        # The feed gave this item another item's description (seen on a live news-agency feed):
        # keep the headline and link, drop the mismatched text so extraction is not misled.
        summary, excerpt_note = "", "description identical to another item of this feed with a different headline; not used"
    text = f"{title} {summary}".strip()
    lang, lang_conf = detect_language(text, item.language_hint or source["language"])
    content_hash = hashlib.sha256(match_form(text).encode("utf-8")).hexdigest()
    sh = simhash(text)
    own_origin = f"origin:{source.get('origin_group') or source['id']}"
    parent, sim = _near_duplicate(conn, source["tenant_id"], sh, text, content_hash, str(source["id"]), title, summary,
                                  own_origin)
    wire = detect_wire(text)
    if not wire and parent and parent.get("attributed_origin"):
        wire = parent["attributed_origin"]
    okey = origin_key(source, item, wire, parent)
    flags = injection.flags(text)
    meta = {"origin_key": okey, "guid": item.guid, "published_precision": item.published_precision}
    if excerpt_note:
        meta["excerpt_note"] = excerpt_note
    if item.publisher_domain:
        meta["publisher_domain"] = item.publisher_domain
    row = fetch_one(conn, """
        insert into articles(tenant_id, source_id, url, canonical_url, url_hash, title, excerpt, language,
          language_confidence, published_at, published_at_source, author, content_hash, simhash,
          syndicated_from_article_id, syndication_similarity, attributed_origin, is_original, license_type,
          usage_policy, injection_flags, is_demo, meta, status, title_en, excerpt_en)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (tenant_id, url_hash) do nothing
        returning id""",
                    (source["tenant_id"], source["id"], item.url, canonical, h, title, excerpt_for(source, summary), lang,
                     lang_conf, item.published_at, item.published_raw, clean_text(item.author, 200) or None, content_hash,
                     to_signed(sh), parent["id"] if parent else None, round(sim, 3) if parent else None, wire,
                     parent is None, source["license_type"], source["usage_policy"], flags, source["is_demo"],
                     jsonb(meta), "new",
                     title if lang == "en" else None, excerpt_for(source, summary) if lang == "en" else None))
    if not row:
        return None
    aid = row["id"]
    execute(conn, """insert into claim_provenance(tenant_id, article_id, relation, target_label)
                     values (%s, %s, 'reported_by', %s)""", (source["tenant_id"], aid, source["publisher"]))
    if parent:
        execute(conn, """insert into claim_provenance(tenant_id, article_id, relation, target_article_id, weight)
                         values (%s, %s, 'copied_from', %s, %s)""", (source["tenant_id"], aid, parent["id"], round(sim, 3)))
    if wire:
        execute(conn, """insert into claim_provenance(tenant_id, article_id, relation, target_label)
                         values (%s, %s, 'attributed_to', %s)""", (source["tenant_id"], aid, f"wire:{wire}"))
    return str(aid)


def _record_health(conn: psycopg.Connection, source: dict, result: AdapterResult, found: int, new: int,
                   error: str | None) -> None:
    execute(conn, """
        insert into source_health(tenant_id, source_id, ok, http_status, robots_allowed, items_found, items_new,
                                  latency_ms, error)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (source["tenant_id"], source["id"], error is None, result.http_status, result.robots_allowed, found, new,
             result.latency_ms, error))


def ingest_source(conn: psycopg.Connection, source: dict, fetcher: PoliteFetcher) -> dict:
    t0 = time.monotonic()
    adapter = adapter_for(source, fetcher)
    if adapter is None:
        result = AdapterResult(items=[], error=f"adapter '{source['adapter']}' is not implemented")
    else:
        try:
            result = adapter.fetch(source)
        except Exception as exc:  # adapters should not raise, but never let one source break the run
            log.exception("adapter crashed", extra={"source_id": str(source["id"])})
            result = AdapterResult(items=[], error=f"{type(exc).__name__}: {exc}"[:300])
    new_ids: list[str] = []
    if result.error:
        failures = int(source["consecutive_failures"]) + 1
        backoff = FAILURE_BACKOFF_MINUTES[min(failures, len(FAILURE_BACKOFF_MINUTES)) - 1]
        if result.retry_after_seconds:  # the server said when to come back (429/503 Retry-After)
            backoff = max(backoff, -(-result.retry_after_seconds // 60))
        execute(conn, """
            update sources set last_error = %s, last_error_at = now(), consecutive_failures = %s,
                   next_fetch_at = now() + make_interval(mins => greatest(fetch_interval_minutes, %s))
            where id = %s""", (result.error[:500], failures, backoff, source["id"]))
        _record_health(conn, source, result, 0, 0, result.error[:500])
        log.warning("source fetch failed", extra={"source": source["name"], "error": result.error})
    else:
        for item in result.items[:MAX_ITEMS_PER_FETCH]:
            try:
                with conn.transaction():
                    aid = store_item(conn, source, item)
            except psycopg.Error as exc:
                log.warning("item rejected", extra={"url": item.url, "error": str(exc)[:200]})
                continue
            if aid:
                new_ids.append(aid)
        execute(conn, """
            update sources set etag = coalesce(%s, etag), last_modified = coalesce(%s, last_modified),
                   last_success_at = now(), consecutive_failures = 0, last_error = null,
                   next_fetch_at = now() + make_interval(mins => fetch_interval_minutes)
            where id = %s""", (result.etag, result.last_modified, source["id"]))
        _record_health(conn, source, result, len(result.items), len(new_ids), None)
    return {"source_id": str(source["id"]), "source": source["name"], "found": len(result.items),
            "new": len(new_ids), "article_ids": new_ids, "error": result.error, "not_modified": result.not_modified,
            "ms": int((time.monotonic() - t0) * 1000)}


def now_utc() -> datetime:
    return datetime.now(UTC)


def stale_cutoff(hours: int = 72) -> datetime:
    return now_utc() - timedelta(hours=hours)
