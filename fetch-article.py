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
from html.parser import HTMLParser

TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (X11; Linux) omarchy-news-feed/1.0"
MAX_CHARS = 8000
MIN_READABLE_CHARS = 200
MAX_BYTES = 5 * 1024 * 1024  # 5 MiB cap on article responses
MAX_TITLE_LEN = 300
MAX_URL_LEN = 2048

SKIP_TAGS = {
    "script", "style", "noscript", "svg", "header", "footer", "nav",
    "aside", "form", "button", "select", "textarea", "iframe",
}
BREAK_TAGS = {
    "p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "article", "section", "tr",
}


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
                self.sock = socket.create_connection(sockaddr, timeout=self.timeout)
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
                sock = socket.create_connection(sockaddr, timeout=self.timeout)
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


class ArticleParser(HTMLParser):
    """Collects paragraph-ish text blocks and a title while skipping chrome."""

    # Model-cardinality caps: a tiny response must not amplify into huge collections.
    MAX_BLOCKS = 600
    MAX_TOTAL_CHARS = 80_000
    MAX_BUF_CHARS = 20_000

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.title = ""
        self._in_title = False
        self.paragraphs = []
        self._buf = []
        self._total_chars = 0
        self._aborted = False
        self._buf_chars = 0

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
        if self.skip_depth or self._aborted:
            return
        if self._in_title and not self.title:
            self.title += data
            if len(self.title) > MAX_TITLE_LEN:
                self.title = self.title[:MAX_TITLE_LEN]
        # bound the pending buffer to avoid accumulation amplification
        if self._buf_chars + len(data) > self.MAX_BUF_CHARS:
            # drop excess rather than grow unbounded
            remaining = self.MAX_BUF_CHARS - self._buf_chars
            if remaining > 0:
                self._buf.append(data[:remaining])
                self._buf_chars += remaining
            return
        self._buf.append(data)
        self._buf_chars += len(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        self._buf_chars = 0
        if not text or self._aborted:
            return
        if len(self.paragraphs) >= self.MAX_BLOCKS:
            self._aborted = True
            return
        if self._total_chars + len(text) > self.MAX_TOTAL_CHARS:
            remaining = self.MAX_TOTAL_CHARS - self._total_chars
            if remaining <= 0:
                self._aborted = True
                return
            text = text[:remaining]
            self._aborted = True
        self.paragraphs.append(text)
        self._total_chars += len(text)

    def close(self):
        self._flush()
        super().close()


def extract(page_html):
    parser = ArticleParser()
    parser.feed(page_html)
    parser.close()

    title = html.unescape(parser.title.strip())[:MAX_TITLE_LEN]
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
        raw = chunk
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
    if len(url) > MAX_URL_LEN:
        url = url[:MAX_URL_LEN]

    parsed = urllib.parse.urlparse(url)
    if not parsed.hostname or not _is_safe_host(parsed.hostname):
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
