#!/usr/bin/env python3
"""DealFlow M&A News API + static file server."""

import http.server
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

# ── Config ────────────────────────────────────────────────────────────────────

PORT      = int(os.environ.get('PORT', 8000))
CACHE_TTL = 300   # 5 minutes
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

FEEDS = [
    # Reuters M&A specific feed — highest quality
    {"name": "Reuters M&A",       "short": "Reuters",
     "url": "https://feeds.reuters.com/reuters/mergersNews"},
    {"name": "Reuters Business",  "short": "Reuters",
     "url": "https://feeds.reuters.com/reuters/businessNews"},
    # WSJ Deal Journal
    {"name": "WSJ Deal Journal",  "short": "WSJ",
     "url": "https://feeds.a.dj.com/rss/RSSWSJD.xml"},
    {"name": "WSJ Markets",       "short": "WSJ",
     "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"},
    # FT M&A
    {"name": "FT M&A",            "short": "FT",
     "url": "https://www.ft.com/rss/companies/mergers-acquisitions"},
    # TechCrunch M&A tag
    {"name": "TechCrunch M&A",    "short": "TechCrunch",
     "url": "https://techcrunch.com/tag/mergers-and-acquisitions/feed/"},
    # CNBC M&A
    {"name": "CNBC M&A",          "short": "CNBC",
     "url": "https://search.cnbc.com/rs/search/combinedcgi?m=20&RestrictedQuery=mergers+acquisitions&source=15839135"},
    # NYT Business
    {"name": "NYT Business",      "short": "NYT",
     "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"},
    # BBC Business
    {"name": "BBC Business",      "short": "BBC",
     "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    # Google News — tightly scoped, no stock-purchase noise
    {"name": "Google News M&A",   "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22mergers+and+acquisitions%22&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News Acquisition Billion", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22acquisition%22+%22billion%22+-shares+-MarketBeat&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News Merger", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22merger%22+%22deal%22+-shares+-MarketBeat&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News Buyout", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22buyout%22+OR+%22private+equity%22+%22acquires%22+-shares&hl=en-US&gl=US&ceid=US:en"},
    # Bloomberg & FT via Google News
    {"name": "Bloomberg M&A",     "short": "Bloomberg",
     "url": "https://news.google.com/rss/search?q=site:bloomberg.com+acquisition+OR+merger+OR+buyout&hl=en-US&gl=US&ceid=US:en"},
    {"name": "FT Deals",          "short": "FT",
     "url": "https://news.google.com/rss/search?q=site:ft.com+acquisition+OR+merger+OR+takeover&hl=en-US&gl=US&ceid=US:en"},
]

# ── Noise filter (fast regex, runs before Claude) ─────────────────────────────

NOISE_DOMAINS = [
    'marketbeat.com', 'benzinga.com', 'prnewswire.com',
    'businesswire.com', 'accesswire.com', 'globenewswire.com',
    'stockanalysis.com', 'wisesheets.io',
]

NOISE_PATTERNS = [
    r'\b\d[\d,]*\s+shares?\s+(of|in)\b',
    r'\b(buys?|acquires?|purchased?)\s+\d[\d,]*\s+shares?',
    r'\bincreases?\s+(its\s+)?(stake|position|holdings)\b',
    r'\b(price\s+target|analyst|downgrade|upgrade|overweight|outperform)\b',
    r'\b(dividend|stock\s+split|share\s+buyback|repurchase\s+program)\b',
    r'\b\d[\d,]*\s+(common\s+)?shares?\s+of\s+[A-Z]',
    r'\bLLC\s+acquires?\s+\d',
    r'\bLP\s+acquires?\s+\d',
    r'\b(q[1-4]|first|second|third|fourth)\s+quarter\b',
    r'\b(earnings?|eps|revenue)\s+(results?|report|beat|miss)\b',
    r'\bInc\.\s+acquires?\s+\d[\d,]*\s+shares',
]

def is_noise(title, link):
    if any(d in link.lower() for d in NOISE_DOMAINS):
        return True
    for pat in NOISE_PATTERNS:
        if re.search(pat, title, re.I):
            return True
    return False

# ── MA keywords & sector detection ───────────────────────────────────────────

MA_KEYWORDS = [
    'acqui', 'merger', 'takeover', 'buyout', 'acquisition',
    'to buy', 'to acquire', 'combine', 'merge', 'divest',
    'spin-off', 'spinoff', 'private equity', 'm&a', 'deal valued', 'deal worth',
]

SECTOR_KEYWORDS = {
    'tech':       ['tech', 'software', 'cloud', 'ai', 'data', 'digital', 'cyber',
                   'chip', 'semiconductor', 'saas', 'startup', 'app', 'platform'],
    'finance':    ['bank', 'financ', 'insur', 'credit', 'capital', 'fund', 'asset',
                   'payment', 'fintech', 'invest', 'hedge', 'brokerage', 'exchange'],
    'healthcare': ['health', 'pharma', 'bio', 'medic', 'drug', 'clinic', 'hospital',
                   'therapeut', 'genomic', 'biotech', 'vaccine', 'diagnostics'],
    'energy':     ['energy', 'oil', 'gas', 'renewable', 'solar', 'wind', 'power',
                   'utility', 'mining', 'lithium', 'coal', 'pipeline'],
    'media':      ['media', 'entertain', 'streaming', 'content', 'broadcast', 'studio',
                   'publish', 'music', 'gaming', 'news', 'film', 'television'],
    'retail':     ['retail', 'brand', 'consumer', 'food', 'beverage', 'restaurant',
                   'ecommerce', 'shop', 'grocery', 'fashion', 'luxury'],
}

# ── Claude API filtering (optional — needs ANTHROPIC_API_KEY) ─────────────────

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

def claude_filter(items):
    """Use Claude to keep only real, meaningful M&A deals from known companies."""
    if not ANTHROPIC_API_KEY or not items:
        return items

    kept_all = []
    batch_size = 40

    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        articles_text = '\n'.join([
            f'{i+1}. {item["title"]}'
            for i, item in enumerate(batch)
        ])

        prompt = (
            "You are a senior M&A analyst at a bulge-bracket investment bank. "
            "Review these news headlines and return ONLY the numbers of articles that are "
            "GENUINE corporate M&A events (mergers, acquisitions, buyouts, divestitures, spin-offs) "
            "involving at least one well-known company (public company, Fortune 1000, major tech/startup, "
            "prominent PE firm, or deal ≥$50M).\n\n"
            "EXCLUDE: stock purchases by funds, analyst ratings, earnings, price targets, "
            "dividend announcements, share buybacks, obscure micro-cap deals, and press releases "
            "from unknown companies.\n\n"
            f"Headlines:\n{articles_text}\n\n"
            "Respond with ONLY a JSON array of integers, e.g. [1, 3, 5]. Nothing else."
        )

        try:
            payload = json.dumps({
                "model": "claude-haiku-4-5",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}]
            }).encode()

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
            )
            resp = urllib.request.urlopen(req, timeout=30, context=SSL_CTX)
            result = json.loads(resp.read().decode())
            text = result['content'][0]['text'].strip()

            m = re.search(r'\[[\d,\s]*\]', text)
            if m:
                indices = json.loads(m.group(0))
                kept_all.extend(batch[i - 1] for i in indices if 1 <= i <= len(batch))
            else:
                kept_all.extend(batch)  # fallback: keep all

        except Exception as e:
            print(f"[Claude] Batch {start}-{start+batch_size} error: {e}")
            kept_all.extend(batch)  # fallback on error

    print(f"[Claude] Filtered {len(items)} → {len(kept_all)} items")
    return kept_all

# ── RSS helpers ───────────────────────────────────────────────────────────────

NS = {
    "dc":      "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom":    "http://www.w3.org/2005/Atom",
}

def fetch_url(url, timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 DealFlow/2.0 RSS Reader",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX).read().decode("utf-8", errors="replace")

def _text(el):
    return (el.text or "").strip() if el is not None else ""

def parse_rss(xml_text, feed_meta):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    channel = root.find("channel")
    entries = channel.findall("item") if channel is not None else \
              root.findall("{http://www.w3.org/2005/Atom}entry")

    for entry in entries:
        def g(tag):
            for ns in ("", NS["dc"], NS["atom"]):
                el = entry.find(f"{{{ns}}}{tag}" if ns else tag)
                if el is not None:
                    return _text(el)
            return ""

        title = g("title") or _text(entry.find("{http://www.w3.org/2005/Atom}title"))
        if not title:
            continue

        link = g("link")
        if not link:
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            if link_el is not None:
                link = link_el.get("href", "") or _text(link_el)

        description = (g("description") or
                       _text(entry.find("{http://www.w3.org/2005/Atom}summary")) or
                       _text(entry.find(f"{{{NS['content']}}}encoded")))

        pub_date = (g("pubDate") or
                    _text(entry.find("{http://www.w3.org/2005/Atom}published")) or
                    _text(entry.find("{http://www.w3.org/2005/Atom}updated")))

        try:
            dt = parsedate_to_datetime(pub_date)
            pub_iso = dt.isoformat()
        except Exception:
            try:
                dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                pub_iso = dt.isoformat()
            except Exception:
                pub_iso = datetime.now(timezone.utc).isoformat()

        desc = re.sub(r"<[^>]+>", " ", description)
        desc = re.sub(r"&[a-z]+;", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()[:400]

        text = f"{title} {desc}".lower()

        sector = "general"
        for s, kws in SECTOR_KEYWORDS.items():
            if any(k in text for k in kws):
                sector = s
                break

        if re.search(r"blocked|regulat|antitrust|reject|abandon|terminat|halted|called off", text):
            status = "blocked"
        elif re.search(r"complet|closed|finali|approv|signed|consummat", text):
            status = "closed"
        elif re.search(r"rumor|report|consider|explore|talk|eye|weigh|plan|interest|potential|near|mull", text):
            status = "rumor"
        else:
            status = "announced"

        m = re.search(r'\$[\d,.]+\s*(billion|million|trillion|bn|mn)\b', text, re.I)
        deal_value = m.group(0).strip() if m else None

        ma_related = any(kw in text for kw in MA_KEYWORDS)

        items.append({
            "id":          link or title,
            "title":       title,
            "description": desc,
            "link":        link,
            "pubDate":     pub_iso,
            "source":      feed_meta["short"],
            "sourceFull":  feed_meta["name"],
            "sector":      sector,
            "status":      status,
            "dealValue":   deal_value,
            "maRelated":   ma_related,
        })

    return items

def fetch_feed(feed):
    try:
        return parse_rss(fetch_url(feed["url"]), feed)
    except Exception as e:
        print(f"[FEED ERROR] {feed['name']}: {e}")
        return []

# ── Load + cache ──────────────────────────────────────────────────────────────

_cache = {"data": None, "ts": 0}
_lock  = threading.Lock()

def load_all_feeds():
    results = [None] * len(FEEDS)

    def worker(i, feed):
        results[i] = fetch_feed(feed)

    threads = [threading.Thread(target=worker, args=(i, f)) for i, f in enumerate(FEEDS)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=15)

    raw = [item for r in results if r for item in r]

    # Step 1: fast regex noise filter
    raw = [item for item in raw if not is_noise(item["title"], item["link"])]

    # Step 2: deduplicate
    seen, deduped = set(), []
    for item in raw:
        key = item["title"].lower()[:70]
        if key not in seen and item["title"]:
            seen.add(key)
            deduped.append(item)

    # Sort newest first before Claude (so Claude sees the freshest items first)
    deduped.sort(key=lambda x: x["pubDate"], reverse=True)

    # Step 3: Claude quality filter (optional)
    deduped = claude_filter(deduped)

    # Re-sort after filtering
    deduped.sort(key=lambda x: x["pubDate"], reverse=True)
    return deduped

def get_news(force=False):
    with _lock:
        now = time.time()
        if not force and _cache["data"] is not None and (now - _cache["ts"]) < CACHE_TTL:
            return _cache["data"]
        print(f"[CACHE] Refreshing ({len(FEEDS)} feeds, Claude={'on' if ANTHROPIC_API_KEY else 'off'})…")
        data = load_all_feeds()
        _cache["data"] = data
        _cache["ts"]   = now
        print(f"[CACHE] Done — {len(data)} items")
        return data

def search_deals(query):
    encoded = urllib.parse.quote(query)
    feeds = [
        {"name": "Search", "short": "Google News",
         "url": f"https://news.google.com/rss/search?q={encoded}+acquisition+OR+merger+OR+acquired&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Search2", "short": "Google News",
         "url": f"https://news.google.com/rss/search?q={encoded}+deal+OR+buyout+OR+takeover&hl=en-US&gl=US&ceid=US:en"},
    ]
    raw = [item for feed in feeds for item in fetch_feed(feed)]
    raw = [item for item in raw if not is_noise(item["title"], item["link"])]
    seen, out = set(), []
    for item in raw:
        key = item["title"].lower()[:70]
        if key not in seen:
            seen.add(key)
            out.append(item)
    out.sort(key=lambda x: x["pubDate"], reverse=True)
    return out

# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

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
        path   = parsed.path

        if path == "/api/news":
            force = "refresh" in parsed.query
            news  = get_news(force=force)
            self.send_json({"items": news, "count": len(news),
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "claude": bool(ANTHROPIC_API_KEY)})

        elif path == "/api/search":
            q = urllib.parse.parse_qs(parsed.query).get("q", [""])[0].strip()
            if not q:
                self.send_json({"error": "missing q"}, code=400); return
            self.send_json({"items": search_deals(q), "query": q})

        elif path in ("/", "/index.html"):
            self.send_file("index.html", "text/html; charset=utf-8")

        else:
            self.send_error(404)

if __name__ == "__main__":
    print(f"[DealFlow] http://0.0.0.0:{PORT}  Claude={'enabled' if ANTHROPIC_API_KEY else 'disabled (set ANTHROPIC_API_KEY)'}")
    threading.Thread(target=get_news, daemon=True).start()
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
