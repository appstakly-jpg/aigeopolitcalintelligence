"""Report rendering: HTML (Jinja2, autoescaped), DOCX (python-docx) and PDF (headless Chromium)."""

from __future__ import annotations

import io
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from ..log import get_logger

log = get_logger(__name__)
TEMPLATES = Path(__file__).resolve().parent / "templates"
_CITE = re.compile(r"\[(\d{1,3})\]")


def _cite_filter(text: str) -> Markup:
    safe = str(escape(text or ""))
    # Input is escaped first; only digit-only citation markers are turned into links.
    return Markup(_CITE.sub(lambda m: f'<a href="#c{m.group(1)}"><sup>[{m.group(1)}]</sup></a>', safe))  # noqa: S704


_env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]))
_env.filters["cite"] = _cite_filter


def render_html(content: dict, citations: list[dict], meta: dict, check: dict | None = None) -> str:
    return _env.get_template("report.html.j2").render(content=content, citations=citations, meta=meta, check=check)


def render_docx(content: dict, citations: list[dict], meta: dict) -> bytes:
    from docx import Document  # noqa: PLC0415
    from docx.shared import Pt, RGBColor  # noqa: PLC0415

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    if content.get("is_demo"):
        p = doc.add_paragraph()
        r = p.add_run("SYNTHETIC DEMO DATA - these events are fabricated test fixtures, not real news.")
        r.bold = True
        r.font.color.rgb = RGBColor(0x9A, 0x34, 0x12)
    doc.add_paragraph(f"Unclassified - public-source intelligence | {meta['ref']} v{meta['version']} | {meta['status'].upper()}")
    doc.add_heading(content["title"], level=0)
    doc.add_paragraph(f"Period: {content['period']['start'][:16].replace('T', ' ')} to "
                      f"{content['period']['end'][:16].replace('T', ' ')} UTC")
    for s in content["sections"]:
        doc.add_heading(s["heading"], level=1)
        for p in s.get("paragraphs", []):
            doc.add_paragraph(p["text"])
        for it in s.get("items", []):
            doc.add_paragraph(f"{it['incident_ref']} | severity {it['severity']}/5 | "
                              f"{it['verification'].replace('_', ' ')} - {it['text']}", style="List Bullet")
        for b in s.get("bullets", []) or []:
            doc.add_paragraph(b, style="List Bullet")
        if s.get("empty"):
            doc.add_paragraph(s["empty"]).italic = True
    doc.add_heading("Sources cited", level=1)
    for c in citations:
        doc.add_paragraph(f"[{c['n']}] {c['publisher']} (reliability {c['reliability']}) - {c['title']}"
                          f"{' [EN: ' + c['title_en'] + ']' if c.get('title_en') and c['title_en'] != c['title'] else ''}. "
                          f"Published {(c.get('published_at') or 'unknown')[:16]}. {c['url']}")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def render_pdf(html: str) -> bytes:
    """Requires Chromium via Playwright (`pip install .[pdf]`); raises RuntimeError when unavailable."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("PDF export needs the 'pdf' extra (playwright) and a Chromium binary") from exc
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page()
            # Offline render: block all network requests from the report HTML.
            page.route("**/*", lambda route: route.abort() if not route.request.url.startswith("data:") else route.continue_())
            page.set_content(html, wait_until="domcontentloaded")
            return page.pdf(format="A4", print_background=True,
                            margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
        finally:
            browser.close()
