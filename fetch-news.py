#!/usr/bin/env python3
"""Fetch an RSS feed and print a JSON array of headlines.

    fetch-news.py <feed-url> [limit]

Read-only: the feed is fetched over HTTPS/HTTP and nothing is written to
disk. Only the Python standard library is used (urllib, xml.etree,
email.utils), so no extra packages need to be installed. Any network,
timeout, or parse failure prints an empty array rather than raising, so the
panel always gets valid JSON.
"""
import html
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 (X11; Linux) omarchy-news-feed/1.0"
SNIPPET_MAX = 160


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def epoch_of(pub_date):
    """Parse RFC 822 (most RSS feeds) or ISO 8601 (e.g. Yahoo Finance) dates."""
    if not pub_date:
        return 0
    dt = None
    try:
        dt = parsedate_to_datetime(pub_date)
    except (TypeError, ValueError):
        dt = None
    if dt is None or dt.tzinfo is None:
        try:
            dt = datetime.fromisoformat(pub_date.strip().replace("Z", "+00:00"))
        except ValueError:
            return 0
    if dt.tzinfo is None:
        return 0
    return int(dt.timestamp())


def text_of(el, tag):
    child = el.find(tag)
    if child is None or not child.text:
        return ""
    return child.text.strip()


def fetch(feed_url):
    req = urllib.request.Request(
        feed_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def parse(raw, limit):
    root = ET.fromstring(raw)
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall(".//item")

    out = []
    for item in items[:limit]:
        title = text_of(item, "title")
        if not title:
            continue
        link = text_of(item, "link")
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else ""
        snippet = strip_html(text_of(item, "description"))
        if len(snippet) > SNIPPET_MAX:
            snippet = snippet[: SNIPPET_MAX - 3] + "..."
        out.append(
            {
                "title": html.unescape(title),
                "link": link,
                "source": source,
                "published": epoch_of(text_of(item, "pubDate")),
                "snippet": snippet,
            }
        )
    return out


def main(argv):
    feed_url = argv[1].strip() if len(argv) > 1 else ""
    if not re.match(r"^https?://", feed_url, re.I):
        print("[]")
        return 0

    try:
        limit = int(argv[2])
    except (IndexError, ValueError):
        limit = 25
    limit = max(1, min(50, limit))

    try:
        raw = fetch(feed_url)
        items = parse(raw, limit)
    except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError):
        print("[]")
        return 0

    print(json.dumps(items, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
