#!/usr/bin/env python3
"""Fetch a web page and extract a best-effort readable plain-text version.

    fetch-article.py <url>

Uses only the Python standard library (urllib, html.parser) — no readability
package, no extra dependency beyond Python 3. The extraction heuristic is
simple: collect text from paragraph-like elements, drop short UI/nav
fragments, and join what is left. It works well on server-rendered pages and
badly on JavaScript-rendered ones, since only the initial HTML response is
read; the panel is expected to fall back to opening the real page when the
result is too thin to read.

Prints a JSON object on stdout:
  {"title": str, "text": str, "truncated": bool}   on success
  {"error": str}                                    when nothing readable was found

Network, parse, and decode failures are caught and reported the same way —
this script never raises, so the caller always gets valid JSON.
"""
import html
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser

TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (X11; Linux) omarchy-news-feed/1.0"
MAX_CHARS = 8000
MIN_READABLE_CHARS = 200

SKIP_TAGS = {
    "script", "style", "noscript", "svg", "header", "footer", "nav",
    "aside", "form", "button", "select", "textarea", "iframe",
}
BREAK_TAGS = {
    "p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "article", "section", "tr",
}


class ArticleParser(HTMLParser):
    """Collects paragraph-ish text blocks and a title while skipping chrome."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.title = ""
        self._in_title = False
        self.paragraphs = []
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta" and self.skip_depth == 0 and not self.title:
            attrd = dict(attrs)
            prop = (attrd.get("property") or attrd.get("name") or "").lower()
            if prop in ("og:title", "twitter:title"):
                self.title = (attrd.get("content") or "").strip()
        if tag in BREAK_TAGS:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag in BREAK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self._in_title and not self.title:
            self.title += data
        self._buf.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.paragraphs.append(text)

    def close(self):
        self._flush()
        super().close()


def extract(page_html):
    parser = ArticleParser()
    parser.feed(page_html)
    parser.close()

    title = html.unescape(parser.title.strip())
    title_lower = title.lower()

    # Drop skip-navigation links and paragraphs that just echo the title
    # (with or without a trailing " · Source" byline) — both are common
    # boilerplate ahead of the real article text, not part of it.
    def is_boilerplate(p):
        pl = p.lower()
        if pl.startswith("skip to "):
            return True
        if len(title_lower) > 15 and pl.startswith(title_lower) and len(p) <= len(title) + 40:
            return True
        return False

    candidates = [p for p in parser.paragraphs if not is_boilerplate(p)]

    # Prefer paragraphs with enough prose to be worth reading, which drops
    # most nav/button/menu fragments. If that filter would leave almost
    # nothing, fall back to the unfiltered set rather than show an empty
    # article for a page that just has short paragraphs throughout.
    long_paras = [p for p in candidates if len(p) >= 40]
    paragraphs = long_paras if len(long_paras) >= 3 else candidates

    # Collapse consecutive duplicates, a common repeated-nav artifact.
    deduped = []
    for p in paragraphs:
        if not deduped or deduped[-1] != p:
            deduped.append(p)

    text = "\n\n".join(deduped)
    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0] + "…"

    return title, text, truncated


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def main(argv):
    url = argv[1].strip() if len(argv) > 1 else ""
    if not re.match(r"^https?://", url, re.I):
        print(json.dumps({"error": "Invalid article URL"}))
        return 0

    try:
        page = fetch(url)
    except (urllib.error.URLError, TimeoutError, OSError):
        print(json.dumps({"error": "Could not reach the article"}))
        return 0

    try:
        title, text, truncated = extract(page)
    except Exception:
        # The parser walks arbitrary, often malformed real-world HTML from
        # sites we don't control; fail soft into the same JSON error shape
        # as every other failure mode instead of a Python traceback.
        print(json.dumps({"error": "Could not read this article"}))
        return 0

    if len(text) < MIN_READABLE_CHARS:
        print(json.dumps({"error": "Not enough readable text on this page"}))
        return 0

    print(json.dumps({"title": title, "text": text, "truncated": truncated}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
