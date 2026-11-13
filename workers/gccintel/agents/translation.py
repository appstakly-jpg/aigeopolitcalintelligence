"""Translation Agent (C).

Machine-translates non-English titles/excerpts into English when an LLM is configured, keeping
the original text alongside the translation and recording which model translated it. With no
LLM configured nothing is fabricated: the original-language text is kept and the UI labels it
"not translated". Extraction works natively on Arabic keywords either way.
"""

from __future__ import annotations

import psycopg

from ..db import execute
from ..llm.base import LLMError, LLMUnavailable
from ..llm.service import LLMService
from ..text import injection
from ..text.normalize import clean_text

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title_en", "excerpt_en"],
    "properties": {"title_en": {"type": "string"}, "excerpt_en": {"type": ["string", "null"]}},
}
SYSTEM = ("Translate the news headline and excerpt into neutral, literal English. Preserve names, numbers, dates "
          "and attributions exactly; do not add, explain or summarise. If the excerpt is empty return null.\n\n"
          + injection.UNTRUSTED_DATA_POLICY)


def translate_article(conn: psycopg.Connection, article: dict, llm: LLMService, max_chars: int) -> bool:
    """Returns True when a translation was stored."""
    if article["language"] == "en" or article.get("title_en") and article.get("excerpt_en"):
        return False
    if not llm.available:
        return False
    user = injection.fence("article", f"{article['title']}\n\n{article.get('excerpt') or ''}",
                           {"language": article["language"]})
    try:
        result = llm.json(agent="translation", task="translation", system=SYSTEM, user=user, schema=SCHEMA,
                          schema_name="translation", max_tokens=1500, effort="low")
    except (LLMUnavailable, LLMError):
        return False
    data = result.data or {}
    title_en = clean_text(data.get("title_en"), 300)
    if not title_en:
        return False
    excerpt_en = clean_text(data.get("excerpt_en"), max_chars) or None
    execute(conn, "update articles set title_en = %s, excerpt_en = %s, translated_by = %s where id = %s",
            (title_en, excerpt_en, f"llm:{result.model}", article["id"]))
    article.update(title_en=title_en, excerpt_en=excerpt_en)
    return True
