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
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 (X11; Linux) omarchy-news-feed/1.0"
SNIPPET_MAX = 160
MAX_BYTES = 5 * 1024 * 1024  # 5 MiB cap on feed responses
MAX_TITLE_LEN = 300
MAX_SOURCE_LEN = 100
MAX_LINK_LEN = 2048
MAX_FEED_URL_LEN = 2048


def _is_safe_host(hostname):
    """Return True if hostname resolves only to global (non-private) addresses."""
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return False
    if not infos:
        return False
    for family, _, _, _, sockaddr in infos:
        addr = ipaddress.ip_address(sockaddr[0])
        if not addr.is_global:
            return False
    return True


class VerifiedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that resolves the hostname itself and connects only to global addresses."""

    def connect(self):
        port = self.port or 80
        try:
            infos = socket.getaddrinfo(self.host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except (socket.gaierror, OSError) as e:
            raise OSError(f"Name resolution failed for {self.host}") from e
        if not infos:
            raise OSError(f"No address found for {self.host}")
        valid = []
        for family, _, _, _, sockaddr in infos:
            addr = ipaddress.ip_address(sockaddr[0])
            if addr.is_global:
                valid.append((family, sockaddr))
        if not valid:
            raise OSError(f"No global address for {self.host}")
        last_err = None
        for family, sockaddr in valid:
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
                if self.timeout is not None:
                    sock.settimeout(self.timeout)
                sock.connect(sockaddr)
                self.sock = sock
                break
            except OSError as e:
                last_err = e
        else:
            raise last_err or OSError(f"Could not connect to {self.host}")


class VerifiedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that resolves the hostname itself and connects only to global addresses."""

    def connect(self):
        port = self.port or 443
        try:
            infos = socket.getaddrinfo(self.host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except (socket.gaierror, OSError) as e:
            raise OSError(f"Name resolution failed for {self.host}") from e
        if not infos:
            raise OSError(f"No address found for {self.host}")
        valid = []
        for family, _, _, _, sockaddr in infos:
            addr = ipaddress.ip_address(sockaddr[0])
            if addr.is_global:
                valid.append((family, sockaddr))
        if not valid:
            raise OSError(f"No global address for {self.host}")
        last_err = None
        for family, sockaddr in valid:
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
                if self.timeout is not None:
                    sock.settimeout(self.timeout)
                sock.connect(sockaddr)
                if self._tunnel_host:
                    self.sock = sock
                    self._tunnel()
                    context = self._context or ssl.create_default_context()
                    self.sock = context.wrap_socket(self.sock, server_hostname=self.host)
                else:
                    context = self._context or ssl.create_default_context()
                    self.sock = context.wrap_socket(sock, server_hostname=self.host)
                break
            except OSError as e:
                try:
                    sock.close()
                except Exception:
                    pass
                last_err = e
        else:
            raise last_err or OSError(f"Could not connect to {self.host}")


class VerifiedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(VerifiedHTTPConnection, req)


class VerifiedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(VerifiedHTTPSConnection, req)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that blocks redirects to non-global hosts and non-HTTP(S) schemes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            raise urllib.error.URLError(
                f"Redirect to non-HTTP(S) scheme rejected: {parsed.scheme}"
            )
        hostname = parsed.hostname
        if not hostname or not _is_safe_host(hostname):
            raise urllib.error.URLError(
                f"Redirect to unsafe host rejected: {hostname}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        VerifiedHTTPHandler,
        VerifiedHTTPSHandler,
        _SafeRedirectHandler,
    )
    with opener.open(req, timeout=TIMEOUT) as resp:
        chunk = resp.read(MAX_BYTES + 1)
        if len(chunk) > MAX_BYTES:
            raise urllib.error.URLError("Response exceeds maximum allowed size")
        return chunk


def parse(raw, limit):
    # Incremental parse with cardinality caps to bound CPU/memory amplification.
    # A small 5 MiB response can otherwise materialize huge node collections.
    import io
    MAX_ITEMS_SCANNED = 200
    MAX_TOTAL_CHARS = 120_000
    total_chars = 0
    out = []
    items_seen = 0
    # ET.iterparse over <item> ends keeps only one item's subtree in memory.
    ctx = ET.iterparse(io.BytesIO(raw), events=("end",))
    for _event, elem in ctx:
        if elem.tag != "item":
            continue
        items_seen += 1
        if items_seen > MAX_ITEMS_SCANNED:
            raise ET.ParseError("item cardinality exceeded")
        if len(out) >= limit:
            elem.clear()
            # still need to count scanned items for the cap, so continue
            continue
        def _t(tag):
            c = elem.find(tag)
            return c.text.strip() if c is not None and c.text else ""
        title = _t("title")
        if not title:
            elem.clear()
            continue
        link = _t("link")
        source_el = elem.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else ""
        snippet = strip_html(_t("description"))
        if len(snippet) > SNIPPET_MAX:
            snippet = snippet[: SNIPPET_MAX - 3] + "..."
        title_c = html.unescape(title)[:MAX_TITLE_LEN]
        link_c = link[:MAX_LINK_LEN]
        source_c = source[:MAX_SOURCE_LEN]
        item_chars = len(title_c) + len(link_c) + len(source_c) + len(snippet)
        total_chars += item_chars
        if total_chars > MAX_TOTAL_CHARS:
            raise ET.ParseError("aggregate char budget exceeded")
        out.append(
            {
                "title": title_c,
                "link": link_c,
                "source": source_c,
                "published": epoch_of(_t("pubDate")),
                "snippet": snippet,
            }
        )
        elem.clear()
    return out


def main(argv):
    feed_url = argv[1].strip() if len(argv) > 1 else ""
    if not re.match(r"^https?://", feed_url, re.I):
        print("[]")
        return 0
    if len(feed_url) > MAX_FEED_URL_LEN:
        feed_url = feed_url[:MAX_FEED_URL_LEN]

    parsed = urllib.parse.urlparse(feed_url)
    if not parsed.hostname or not _is_safe_host(parsed.hostname):
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
