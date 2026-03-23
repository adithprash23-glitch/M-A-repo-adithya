#!/usr/bin/env python3
"""DealFlow M&A News API + static file server."""

import http.server
import json
import ssl
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, HTTPServer

# ── Config ───────────────────────────────────────────────────────────────────

PORT = int(__import__('os').environ.get('PORT', 8000))
CACHE_TTL = 300  # 5 minutes

FEEDS = [
    # Google News — targeted M&A queries
    {"name": "Google News M&A", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=mergers+acquisitions+deal&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News Acquired By", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22acquired+by%22+OR+%22acquires%22&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News Acquisition Billion", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22acquisition%22+OR+%22takeover%22+%22billion%22&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News Buyout Merger", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22buyout%22+OR+%22merger%22+%22deal%22+company&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News PE Deals", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22private+equity%22+%22acquires%22+OR+%22buyout%22&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News Tech M&A", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=tech+startup+%22acquired%22+OR+%22acquisition%22&hl=en-US&gl=US&ceid=US:en"},
    # Specialist sources
    {"name": "TechCrunch", "short": "TechCrunch",
     "url": "https://techcrunch.com/feed/"},
    {"name": "TechCrunch M&A Tag", "short": "TechCrunch",
     "url": "https://techcrunch.com/tag/mergers-and-acquisitions/feed/"},
    {"name": "Reuters Business", "short": "Reuters",
     "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "BBC Business", "short": "BBC",
     "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "NYT Business", "short": "NYT",
     "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"},
    {"name": "CNBC M&A", "short": "CNBC",
     "url": "https://search.cnbc.com/rs/search/combinedcgi?m=20&RestrictedQuery=mergers+acquisitions&source=15839135"},
    {"name": "WSJ Markets", "short": "WSJ",
     "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"},
    {"name": "Seeking Alpha M&A", "short": "Seeking Alpha",
     "url": "https://seekingalpha.com/tag/ma.xml"},
]

MA_KEYWORDS = [
    'acqui', 'merger', 'takeover', 'buyout', 'deal', 'bid', 'acquisition',
    'purchase', 'stake', 'combine', 'merge', 'divest', 'spin-off', 'spinoff',
    'private equity', 'm&a', 'billion', 'million dollar',
]

SECTOR_KEYWORDS = {
    'tech':       ['tech', 'software', 'cloud', 'ai', 'data', 'digital', 'cyber', 'chip', 'semiconductor', 'saas', 'startup'],
    'finance':    ['bank', 'financ', 'insur', 'credit', 'capital', 'fund', 'asset', 'payment', 'fintech', 'invest'],
    'healthcare': ['health', 'pharma', 'bio', 'medic', 'drug', 'clinic', 'hospital', 'therapeut', 'genomic'],
    'energy':     ['energy', 'oil', 'gas', 'renewable', 'solar', 'wind', 'power', 'utility', 'mining', 'lithium'],
    'media':      ['media', 'entertain', 'streaming', 'content', 'broadcast', 'studio', 'publish', 'music', 'gaming'],
    'retail':     ['retail', 'brand', 'consumer', 'food', 'beverage', 'restaurant', 'ecommerce', 'shop', 'grocery'],
}

# ── Cache ─────────────────────────────────────────────────────────────────────

_cache = {"data": None, "ts": 0}
_cache_lock = threading.Lock()

# ── Helpers ───────────────────────────────────────────────────────────────────

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

def fetch_url(url, timeout=10):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 DealFlow/1.0 RSS Reader",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
    return resp.read().decode("utf-8", errors="replace")

NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
    "atom": "http://www.w3.org/2005/Atom",
}

def _text(el):
    return (el.text or "").strip() if el is not None else ""

def parse_rss(xml_text, feed_meta):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    # Handle both RSS 2.0 and Atom
    channel = root.find("channel")
    if channel is not None:
        entries = channel.findall("item")
    else:
        entries = root.findall("{http://www.w3.org/2005/Atom}entry")

    for entry in entries:
        def g(tag):
            el = entry.find(tag)
            if el is None:
                el = entry.find(f"{{{NS['dc']}}}{tag}")
            if el is None:
                el = entry.find(f"{{{NS['atom']}}}{tag}")
            return _text(el)

        title = g("title") or _text(entry.find("{http://www.w3.org/2005/Atom}title"))

        # Link: text content OR href attribute (Atom)
        link = g("link")
        if not link:
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            if link_el is not None:
                link = link_el.get("href", "") or _text(link_el)

        description = (g("description") or
                       _text(entry.find("{http://www.w3.org/2005/Atom}summary")) or
                       _text(entry.find("{http://www.w3.org/2005/Atom}content")) or
                       _text(entry.find(f"{{{NS['content']}}}encoded")))
        pub_date = (g("pubDate") or
                    _text(entry.find("{http://www.w3.org/2005/Atom}published")) or
                    _text(entry.find("{http://www.w3.org/2005/Atom}updated")))
        guid = g("guid") or link

        # Parse date
        try:
            dt = parsedate_to_datetime(pub_date)
            pub_iso = dt.isoformat()
        except Exception:
            try:
                dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                pub_iso = dt.isoformat()
            except Exception:
                pub_iso = datetime.now(timezone.utc).isoformat()

        # Strip HTML from description
        import re
        desc = re.sub(r"<[^>]+>", " ", description)
        desc = re.sub(r"&[a-z]+;", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()[:400]

        text = f"{title} {desc}".lower()
        sector = "general"
        for s, kws in SECTOR_KEYWORDS.items():
            if any(k in text for k in kws):
                sector = s
                break

        # Status detection
        if re.search(r"blocked|regulat|antitrust|reject|abandon|terminat|halted", text):
            deal_status = "blocked"
        elif re.search(r"complet|closed|finali|approv|sign", text):
            deal_status = "closed"
        elif re.search(r"rumor|report|consider|explore|talk|eye|weigh|plan|interest|potential|near", text):
            deal_status = "rumor"
        else:
            deal_status = "announced"

        # Deal value extraction
        m = re.search(r'\$[\d,.]+\s*(billion|million|trillion|bn|mn)\b', text, re.I)
        deal_value = m.group(0).strip() if m else None

        # M&A relevance
        ma_related = any(kw in text for kw in MA_KEYWORDS)

        items.append({
            "id": guid,
            "title": title,
            "description": desc,
            "link": link,
            "pubDate": pub_iso,
            "source": feed_meta["short"],
            "sourceFull": feed_meta["name"],
            "sector": sector,
            "status": deal_status,
            "dealValue": deal_value,
            "maRelated": ma_related,
        })

    return items

def fetch_feed(feed):
    try:
        xml = fetch_url(feed["url"])
        return parse_rss(xml, feed)
    except Exception as e:
        print(f"[FEED ERROR] {feed['name']}: {e}")
        return []

def load_all_feeds():
    threads = []
    results = [None] * len(FEEDS)

    def worker(i, feed):
        results[i] = fetch_feed(feed)

    for i, feed in enumerate(FEEDS):
        t = threading.Thread(target=worker, args=(i, feed))
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=15)

    raw = [item for r in results if r for item in r]

    # Deduplicate by title
    seen = set()
    deduped = []
    for item in raw:
        key = item["title"].lower()[:60]
        if key not in seen and item["title"]:
            seen.add(key)
            deduped.append(item)

    # Sort newest first
    deduped.sort(key=lambda x: x["pubDate"], reverse=True)
    return deduped

def get_news(force=False):
    with _cache_lock:
        now = time.time()
        if not force and _cache["data"] is not None and (now - _cache["ts"]) < CACHE_TTL:
            return _cache["data"]
        print(f"[CACHE] Fetching fresh news ({len(FEEDS)} feeds)…")
        data = load_all_feeds()
        _cache["data"] = data
        _cache["ts"] = now
        print(f"[CACHE] Got {len(data)} items")
        return data

def search_deals(query):
    """Live Google News search for a specific company or deal."""
    import urllib.parse as up
    encoded = up.quote(query)
    feeds = [
        {"name": "Google News Search", "short": "Google News",
         "url": f"https://news.google.com/rss/search?q={encoded}+acquisition+OR+merger+OR+acquired&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News Company", "short": "Google News",
         "url": f"https://news.google.com/rss/search?q={encoded}+deal+OR+buyout+OR+takeover&hl=en-US&gl=US&ceid=US:en"},
    ]
    raw = []
    for feed in feeds:
        raw.extend(fetch_feed(feed))
    # Deduplicate
    seen = set()
    out = []
    for item in raw:
        key = item["title"].lower()[:60]
        if key not in seen and item["title"]:
            seen.add(key)
            out.append(item)
    out.sort(key=lambda x: x["pubDate"], reverse=True)
    return out

# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Quiet logging

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, mime):
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/news":
            force = "refresh" in parsed.query
            news = get_news(force=force)
            self.send_json({"items": news, "count": len(news), "ts": datetime.now(timezone.utc).isoformat()})

        elif path == "/api/search":
            qs = urllib.parse.parse_qs(parsed.query)
            query = qs.get("q", [""])[0].strip()
            if not query:
                self.send_json({"error": "missing q param"}, code=400)
                return
            results = search_deals(query)
            self.send_json({"items": results, "count": len(results), "query": query})

        elif path == "/" or path == "/index.html":
            self.send_file("index.html", "text/html; charset=utf-8")

        else:
            self.send_error(404)


if __name__ == "__main__":
    print(f"[DealFlow] Starting server on http://0.0.0.0:{PORT}")
    print(f"[DealFlow] Pre-fetching news…")
    # Pre-warm cache in background
    threading.Thread(target=get_news, daemon=True).start()
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
