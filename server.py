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

PORT               = int(os.environ.get('PORT', 8000))
CACHE_TTL          = 300
ANTHROPIC_API_KEY  = os.environ.get('ANTHROPIC_API_KEY', '')
GEMINI_API_KEY     = os.environ.get('GEMINI_API_KEY', '')

FEEDS = [
    {"name": "Reuters M&A",       "short": "Reuters",
     "url": "https://feeds.reuters.com/reuters/mergersNews"},
    {"name": "Reuters Business",  "short": "Reuters",
     "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "WSJ Deal Journal",  "short": "WSJ",
     "url": "https://feeds.a.dj.com/rss/RSSWSJD.xml"},
    {"name": "WSJ Markets",       "short": "WSJ",
     "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"},
    {"name": "FT M&A",            "short": "FT",
     "url": "https://www.ft.com/rss/companies/mergers-acquisitions"},
    {"name": "TechCrunch M&A",    "short": "TechCrunch",
     "url": "https://techcrunch.com/tag/mergers-and-acquisitions/feed/"},
    {"name": "CNBC M&A",          "short": "CNBC",
     "url": "https://search.cnbc.com/rs/search/combinedcgi?m=20&RestrictedQuery=mergers+acquisitions&source=15839135"},
    {"name": "NYT Business",      "short": "NYT",
     "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"},
    {"name": "BBC Business",      "short": "BBC",
     "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "Google News M&A",   "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22mergers+and+acquisitions%22&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News Acquisition Billion", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22acquisition%22+%22billion%22+-shares+-MarketBeat&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News Merger", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22merger%22+%22deal%22+-shares+-MarketBeat&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News Buyout", "short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22buyout%22+OR+%22private+equity%22+%22acquires%22+-shares&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Bloomberg M&A",     "short": "Bloomberg",
     "url": "https://news.google.com/rss/search?q=site:bloomberg.com+acquisition+OR+merger+OR+buyout&hl=en-US&gl=US&ceid=US:en"},
    {"name": "FT Deals",          "short": "FT",
     "url": "https://news.google.com/rss/search?q=site:ft.com+acquisition+OR+merger+OR+takeover&hl=en-US&gl=US&ceid=US:en"},
    # ── India-specific feeds ──────────────────────────────────────────────────
    {"name": "ET M&A",            "short": "ET",
     "url": "https://economictimes.indiatimes.com/markets/mergers-acquisitions/rss.cms"},
    {"name": "ET Markets",        "short": "ET",
     "url": "https://economictimes.indiatimes.com/markets/rss.cms"},
    {"name": "Livemint Companies","short": "Livemint",
     "url": "https://www.livemint.com/rss/companies"},
    {"name": "Business Standard M&A","short": "Biz Standard",
     "url": "https://www.business-standard.com/rss/mergers-acquisitions-104.rss"},
    {"name": "VCCircle Deals",    "short": "VCCircle",
     "url": "https://news.google.com/rss/search?q=site:vccircle.com+acquisition+OR+merger+OR+funding&hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Google News India M&A","short": "Google News",
     "url": "https://news.google.com/rss/search?q=%22acquisition%22+OR+%22merger%22+India&hl=en-IN&gl=IN&ceid=IN:en"},
    {"name": "Google News India Deals","short": "Google News",
     "url": "https://news.google.com/rss/search?q=India+%22acquires%22+OR+%22buyout%22+%22crore%22+OR+%22billion%22&hl=en-IN&gl=IN&ceid=IN:en"},
]

# ── Noise filter ──────────────────────────────────────────────────────────────

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
    r'\bLLC\s+acquires?\s+\d', r'\bLP\s+acquires?\s+\d',
    r'\b(q[1-4]|first|second|third|fourth)\s+quarter\b',
    r'\b(earnings?|eps|revenue)\s+(results?|report|beat|miss)\b',
]

def is_noise(title, link):
    if any(d in link.lower() for d in NOISE_DOMAINS): return True
    return any(re.search(p, title, re.I) for p in NOISE_PATTERNS)

# ── M&A keywords & sectors ────────────────────────────────────────────────────

MA_KEYWORDS = ['acqui','merger','takeover','buyout','acquisition','to buy','to acquire',
               'combine','merge','divest','spin-off','spinoff','private equity','m&a',
               'deal valued','deal worth']

SECTOR_KEYWORDS = {
    'tech':       ['tech','software','cloud','ai','data','digital','cyber','chip','semiconductor','saas','startup','app','platform'],
    'finance':    ['bank','financ','insur','credit','capital','fund','asset','payment','fintech','invest','hedge','brokerage','exchange'],
    'healthcare': ['health','pharma','bio','medic','drug','clinic','hospital','therapeut','genomic','biotech','vaccine'],
    'energy':     ['energy','oil','gas','renewable','solar','wind','power','utility','mining','lithium','coal','pipeline'],
    'media':      ['media','entertain','streaming','content','broadcast','studio','publish','music','gaming','news','film'],
    'retail':     ['retail','brand','consumer','food','beverage','restaurant','ecommerce','shop','grocery','fashion','luxury'],
}

# ── Region detection ──────────────────────────────────────────────────────────

INDIA_TERMS = [
    'india', 'indian', 'mumbai', 'delhi', 'bengaluru', 'bangalore', 'hyderabad',
    'chennai', 'kolkata', 'pune', 'ahmedabad', 'sebi', 'rbi', 'nse', 'bse',
    'sensex', 'nifty', 'reliance', 'tata ', 'infosys', 'wipro', 'mahindra',
    'adani', 'bajaj', 'hdfc', 'icici', 'sbi ', 'airtel', 'flipkart', 'zomato',
    'paytm', 'rupee', 'crore', 'lakh', 'swiggy', 'byju', 'zerodha', 'vedanta',
    'ambani', 'birla', 'hinduja', 'tcs', 'ola ', 'meesho', 'dmart', 'jio',
    'razorpay', 'phonepe', 'cred', 'nykaa', 'policybazaar', 'groww',
]
US_TERMS = [
    ' u.s.', 'united states', 'wall street', 'nasdaq', 'nyse', 'silicon valley',
    ' sec ', ' ftc ', ' doj ', 'federal reserve', 'new york city', 'san francisco',
    'washington dc', 'us-based', 'american company',
]
EUROPE_TERMS = [
    'european', ' eu ', 'london', 'paris', 'berlin', 'frankfurt', 'zurich',
    'amsterdam', 'madrid', 'milan', 'ftse', ' dax', 'cac 40', ' ecb ',
    'british', 'french', 'german', 'dutch', 'swiss', 'italian', 'spanish',
    'uk-based', 'europe-based',
]
ASIA_TERMS = [
    'china', 'chinese', 'japan', 'japanese', 'south korea', 'korean',
    'singapore', 'hong kong', 'taiwan', 'indonesia', 'thailand', 'vietnam',
    'alibaba', 'tencent', 'softbank', 'samsung', 'baidu', 'bytedance',
    'southeast asia', 'asean',
]

SOURCE_SCORES = {
    'bloomberg': 40, 'ft': 38, 'wsj': 36, 'reuters': 35,
    'nyt': 28, 'cnbc': 25, 'bbc': 22, 'techcrunch': 20,
    'et': 22, 'livemint': 20, 'biz standard': 18, 'moneycontrol': 16,
    'vccircle': 15,
}

def detect_region(text):
    t = text.lower()
    if any(k in t for k in INDIA_TERMS):  return 'india'
    if any(k in t for k in ASIA_TERMS):   return 'asia'
    if any(k in t for k in EUROPE_TERMS): return 'europe'
    if any(k in t for k in US_TERMS):     return 'us'
    return 'world'

def valNum_py(v):
    """Convert deal value string to float for comparison."""
    if not v: return 0
    m = re.search(r'([\d,.]+)\s*(trillion|billion|bn|million|mn)', v or '', re.I)
    if not m: return 0
    n = float(m.group(1).replace(',', ''))
    u = m.group(2).lower()
    return n*1e12 if u.startswith('t') else n*1e9 if u.startswith('b') else n*1e6

def compute_engagement(item):
    """Score 0-200 estimating how much buzz/attention a deal generates."""
    score = 10
    if item.get('maRelated'): score += 20
    v = valNum_py(item.get('dealValue'))
    if   v >= 1e12: score += 80
    elif v >= 1e10: score += 60
    elif v >= 1e9:  score += 40
    elif v >= 1e8:  score += 20
    elif v > 0:     score += 10
    src = item.get('source', '').lower()
    for k, s in SOURCE_SCORES.items():
        if k in src: score += s; break
    else: score += 8
    try:
        pub = item['pubDate'].replace('Z', '+00:00')
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(pub)).total_seconds()
        if   age < 7200:  score += 25
        elif age < 21600: score += 15
        elif age < 86400: score += 5
    except: pass
    score += {'closed': 15, 'announced': 10, 'blocked': 8, 'rumor': 3}.get(item.get('status', ''), 0)
    return min(score, 200)

# ── SSL ───────────────────────────────────────────────────────────────────────

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ── AI helpers ────────────────────────────────────────────────────────────────

CLAUDE_MODELS = [
    "claude-sonnet-4-5",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
]

def call_claude(prompt):
    """Try each model in CLAUDE_MODELS until one works. Returns (text, error_str)."""
    if not ANTHROPIC_API_KEY: return None, "ANTHROPIC_API_KEY not set"
    last_err = ""
    for model in CLAUDE_MODELS:
        try:
            payload = json.dumps({
                "model": model,
                "max_tokens": 700,
                "messages": [{"role": "user", "content": prompt}]
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages", data=payload,
                headers={"x-api-key": ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            try:
                resp = urllib.request.urlopen(req, timeout=30, context=SSL_CTX)
            except urllib.error.HTTPError as he:
                body = he.read().decode()
                last_err = f"HTTP {he.code} ({model}): {body[:200]}"
                print(f"[Claude] {last_err}")
                continue
            result = json.loads(resp.read().decode())
            text = result['content'][0]['text'].strip()
            print(f"[Claude] OK with model={model}")
            return text, None
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print(f"[Claude] {model} → {last_err}")
    return None, last_err

# Discovered at runtime by list_gemini_models(); cached here
_gemini_model_cache = []   # list of (api_ver, model_name)

def list_gemini_models():
    """
    Call Google's ListModels endpoint to find what models this API key can actually use.
    Returns a list of (api_ver, model_name) tuples ordered by preference.
    """
    if not GEMINI_API_KEY: return []
    found = []
    for api_ver in ("v1beta", "v1"):
        try:
            url = f"https://generativelanguage.googleapis.com/{api_ver}/models?key={GEMINI_API_KEY}&pageSize=50"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            resp = urllib.request.urlopen(req, timeout=10, context=SSL_CTX)
            data = json.loads(resp.read().decode())
            for m in data.get("models", []):
                if "generateContent" not in m.get("supportedGenerationMethods", []):
                    continue
                name = m["name"].replace("models/", "")
                # prefer flash/fast models, skip embedding/aqa models
                if any(x in name for x in ("embed", "aqa", "vision", "retrieval")):
                    continue
                found.append((api_ver, name))
            if found:
                # sort: prefer v1beta gemini-2.x > gemini-1.5 > others
                def rank(t):
                    _, n = t
                    if "2.0" in n or "2.5" in n: return 0
                    if "1.5" in n: return 1
                    return 2
                found.sort(key=rank)
                print(f"[Gemini] ListModels found {len(found)} models: {[m for _,m in found[:4]]}")
                return found
        except Exception as e:
            print(f"[Gemini] ListModels ({api_ver}) error: {e}")
    return []

def call_gemini(prompt):
    """
    Discover available models via ListModels (cached), then try each until one works.
    Returns (text, error_str).
    """
    global _gemini_model_cache
    if not GEMINI_API_KEY: return None, "GEMINI_API_KEY not set"
    # Populate cache on first call
    if not _gemini_model_cache:
        _gemini_model_cache = list_gemini_models()
    if not _gemini_model_cache:
        return None, "No generateContent-capable models found for this API key. Check that the Generative Language API is enabled at console.cloud.google.com and the key is from Google AI Studio (aistudio.google.com)."
    last_err = ""
    for api_ver, model in _gemini_model_cache[:6]:   # try top 6 models
        try:
            url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model}:generateContent?key={GEMINI_API_KEY}"
            payload = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": 700}
            }).encode()
            req = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type": "application/json"})
            try:
                resp = urllib.request.urlopen(req, timeout=30, context=SSL_CTX)
            except urllib.error.HTTPError as he:
                body = he.read().decode()
                last_err = f"HTTP {he.code} ({api_ver}/{model}): {body[:300]}"
                print(f"[Gemini] {last_err}")
                continue
            result = json.loads(resp.read().decode())
            text = result['candidates'][0]['content']['parts'][0]['text'].strip()
            print(f"[Gemini] OK with {api_ver}/{model}")
            return text, None
        except Exception as e:
            last_err = f"{type(e).__name__} ({api_ver}/{model}): {e}"
            print(f"[Gemini] {last_err}")
    return None, last_err

def parse_json_from_text(text):
    """Extract JSON from AI response (handles markdown code blocks)."""
    if not text: return {}
    m = re.search(r'```(?:json)?\s*([\s\S]+?)```', text)
    if m: text = m.group(1)
    m = re.search(r'\{[\s\S]+\}', text)
    if m:
        try: return json.loads(m.group(0))
        except: pass
    return {}

def analyze_deal(title, description, sector, deal_value):
    """Call Claude + Gemini in parallel to explain WHY a deal is happening."""

    # Gather recent headlines for market context
    context_items = _cache.get("data") or []
    headlines = '\n'.join(
        f'- {i["title"]}' for i in context_items[:20] if i.get('maRelated')
    ) or "No recent context available."

    # ── Claude prompt: corporate strategy angle ──
    claude_prompt = f"""You are a Managing Director in M&A Advisory at Goldman Sachs.

DEAL: {title}
DESCRIPTION: {description}
SECTOR: {sector} | VALUE: {deal_value or 'undisclosed'}

RECENT M&A MARKET CONTEXT:
{headlines}

Analyze WHY this deal is happening. Be specific, insightful, and use the market context above to identify trends driving it.

Return ONLY valid JSON (no markdown, no extra text):
{{
  "strategic_tag": "<ONE OF: Acqui-hire | Market Consolidation | Supply Chain Verticalization | Defensive Move | Geographic Expansion | Technology Stack Acquisition | Revenue Diversification | Talent Acquisition | Vertical Integration>",
  "sentiment": "<Bullish|Bearish|Neutral>",
  "sentiment_reason": "<one sentence>",
  "rationale": "<2-3 sentences explaining the core strategic logic>",
  "drivers": ["<driver 1>", "<driver 2>", "<driver 3>"],
  "industry_signal": "<1-2 sentences on what this means for the broader industry>"
}}"""

    # ── Gemini prompt: macro/geopolitical angle ──
    gemini_prompt = f"""You are a macro analyst at Bridgewater Associates.

DEAL: {title}
CONTEXT: {description}
SECTOR: {sector}

RECENT M&A MARKET CONTEXT:
{headlines}

Explain this deal through the lens of macro trends, geopolitics, and technology evolution. What forces make this deal make sense RIGHT NOW?

Return ONLY valid JSON (no markdown, no extra text):
{{
  "macro_context": "<2-3 sentences on macro/tech/geopolitical forces driving this>",
  "timing_rationale": "<1-2 sentences: why now specifically?>",
  "industry_trajectory": "<1-2 sentences: where is this industry heading based on this deal?>"
}}"""

    # Run both in parallel
    results = {"claude": None, "gemini": None, "claude_err": None, "gemini_err": None}

    def run_claude():
        raw, err = call_claude(claude_prompt)
        results["claude"] = parse_json_from_text(raw) if raw else {}
        results["claude_err"] = err

    def run_gemini():
        raw, err = call_gemini(gemini_prompt)
        results["gemini"] = parse_json_from_text(raw) if raw else {}
        results["gemini_err"] = err

    t1 = threading.Thread(target=run_claude)
    t2 = threading.Thread(target=run_gemini)
    t1.start(); t2.start()
    t1.join(timeout=35); t2.join(timeout=35)

    return {
        "claude":      results["claude"]     or {},
        "gemini":      results["gemini"]     or {},
        "has_claude":  bool(ANTHROPIC_API_KEY),
        "has_gemini":  bool(GEMINI_API_KEY),
        "claude_err":  results["claude_err"],
        "gemini_err":  results["gemini_err"],
    }

# ── Claude quality filter ─────────────────────────────────────────────────────

def claude_filter(items):
    if not ANTHROPIC_API_KEY or not items: return items
    kept_all = []
    for start in range(0, len(items), 40):
        batch = items[start:start+40]
        articles_text = '\n'.join(f'{i+1}. {item["title"]}' for i,item in enumerate(batch))
        prompt = (
            "You are a senior M&A analyst. Return ONLY the numbers of articles that are "
            "GENUINE corporate M&A events involving at least one well-known company "
            "(public co, Fortune 1000, notable startup, or deal ≥$50M).\n\n"
            "EXCLUDE: fund share purchases, analyst ratings, earnings, dividends, unknown micro-caps.\n\n"
            f"Headlines:\n{articles_text}\n\n"
            "Respond with ONLY a JSON array of integers, e.g. [1, 3, 5]."
        )
        try:
            raw, err = call_claude(prompt)
            if err: print(f"[Claude filter] {err}")
            m = re.search(r'\[[\d,\s]*\]', raw or '')
            if m:
                indices = json.loads(m.group(0))
                kept_all.extend(batch[i-1] for i in indices if 1 <= i <= len(batch))
            else:
                kept_all.extend(batch)
        except Exception as e:
            print(f"[Claude filter] {e}"); kept_all.extend(batch)
    print(f"[Claude filter] {len(items)} → {len(kept_all)}")
    return kept_all

# ── RSS helpers ───────────────────────────────────────────────────────────────

NS = {"dc": "http://purl.org/dc/elements/1.1/",
      "content": "http://purl.org/rss/1.0/modules/content/",
      "atom": "http://www.w3.org/2005/Atom"}

def fetch_url(url, timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 DealFlow/2.0",
        "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX).read().decode("utf-8", errors="replace")

def _text(el): return (el.text or "").strip() if el is not None else ""

def parse_rss(xml_text, feed_meta):
    items = []
    try: root = ET.fromstring(xml_text)
    except ET.ParseError: return items
    channel = root.find("channel")
    entries = channel.findall("item") if channel is not None else \
              root.findall("{http://www.w3.org/2005/Atom}entry")
    for entry in entries:
        def g(tag):
            for ns in ("", NS["dc"], NS["atom"]):
                el = entry.find(f"{{{ns}}}{tag}" if ns else tag)
                if el is not None: return _text(el)
            return ""
        title = g("title") or _text(entry.find("{http://www.w3.org/2005/Atom}title"))
        if not title: continue
        link = g("link")
        if not link:
            le = entry.find("{http://www.w3.org/2005/Atom}link")
            if le is not None: link = le.get("href","") or _text(le)
        description = (g("description") or
                       _text(entry.find("{http://www.w3.org/2005/Atom}summary")) or
                       _text(entry.find(f"{{{NS['content']}}}encoded")))
        pub_date = (g("pubDate") or
                    _text(entry.find("{http://www.w3.org/2005/Atom}published")) or
                    _text(entry.find("{http://www.w3.org/2005/Atom}updated")))
        try:
            dt = parsedate_to_datetime(pub_date); pub_iso = dt.isoformat()
        except:
            try: dt = datetime.fromisoformat(pub_date.replace("Z","+00:00")); pub_iso = dt.isoformat()
            except: pub_iso = datetime.now(timezone.utc).isoformat()
        desc = re.sub(r"<[^>]+>"," ",description)
        desc = re.sub(r"&[a-z]+;"," ",desc)
        desc = re.sub(r"\s+"," ",desc).strip()[:400]
        text = f"{title} {desc}".lower()
        sector = "general"
        for s,kws in SECTOR_KEYWORDS.items():
            if any(k in text for k in kws): sector = s; break
        if re.search(r"blocked|regulat|antitrust|reject|abandon|terminat|halted|called off",text): status="blocked"
        elif re.search(r"complet|closed|finali|approv|signed|consummat",text): status="closed"
        elif re.search(r"rumor|report|consider|explore|talk|eye|weigh|plan|interest|potential|near|mull",text): status="rumor"
        else: status="announced"
        m = re.search(r'\$[\d,.]+\s*(billion|million|trillion|bn|mn)\b',text,re.I)
        deal_value = m.group(0).strip() if m else None
        ma_related = any(kw in text for kw in MA_KEYWORDS)
        region = detect_region(f"{title} {desc} {link}")
        items.append({"id": link or title, "title": title, "description": desc,
                      "link": link, "pubDate": pub_iso, "source": feed_meta["short"],
                      "sourceFull": feed_meta["name"], "sector": sector, "status": status,
                      "dealValue": deal_value, "maRelated": ma_related, "region": region})
    return items

def fetch_feed(feed):
    try: return parse_rss(fetch_url(feed["url"]), feed)
    except Exception as e: print(f"[FEED ERROR] {feed['name']}: {e}"); return []

# ── Cache ─────────────────────────────────────────────────────────────────────

_cache = {"data": None, "ts": 0}
_lock  = threading.Lock()

def load_all_feeds():
    results = [None]*len(FEEDS)
    def worker(i,feed): results[i]=fetch_feed(feed)
    threads = [threading.Thread(target=worker,args=(i,f)) for i,f in enumerate(FEEDS)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=15)
    raw = [item for r in results if r for item in r]
    raw = [item for item in raw if not is_noise(item["title"],item["link"])]
    seen,deduped = set(),[]
    for item in raw:
        key = item["title"].lower()[:70]
        if key not in seen and item["title"]: seen.add(key); deduped.append(item)
    deduped.sort(key=lambda x:x["pubDate"],reverse=True)
    deduped = claude_filter(deduped)
    deduped.sort(key=lambda x:x["pubDate"],reverse=True)
    for item in deduped:
        item["engagementScore"] = compute_engagement(item)
    return deduped

def get_news(force=False):
    with _lock:
        now = time.time()
        if not force and _cache["data"] is not None and (now-_cache["ts"])<CACHE_TTL:
            return _cache["data"]
        print(f"[CACHE] Refreshing ({len(FEEDS)} feeds, Claude={'on' if ANTHROPIC_API_KEY else 'off'}, Gemini={'on' if GEMINI_API_KEY else 'off'})…")
        data = load_all_feeds()
        _cache["data"] = data; _cache["ts"] = now
        print(f"[CACHE] Done — {len(data)} items")
        return data

def search_deals(query):
    encoded = urllib.parse.quote(query)
    feeds = [
        {"name":"Search","short":"Google News","url":f"https://news.google.com/rss/search?q={encoded}+acquisition+OR+merger+OR+acquired&hl=en-US&gl=US&ceid=US:en"},
        {"name":"Search2","short":"Google News","url":f"https://news.google.com/rss/search?q={encoded}+deal+OR+buyout+OR+takeover&hl=en-US&gl=US&ceid=US:en"},
    ]
    raw = [item for feed in feeds for item in fetch_feed(feed)]
    raw = [item for item in raw if not is_noise(item["title"],item["link"])]
    seen,out = set(),[]
    for item in raw:
        key=item["title"].lower()[:70]
        if key not in seen: seen.add(key); out.append(item)
    out.sort(key=lambda x:x["pubDate"],reverse=True)
    return out

# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass

    def send_json(self,data,code=200):
        body=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",len(body))
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers(); self.wfile.write(body)

    def send_file(self,path,mime):
        try:
            with open(path,"rb") as f: data=f.read()
            self.send_response(200)
            self.send_header("Content-Type",mime)
            self.send_header("Content-Length",len(data))
            self.end_headers(); self.wfile.write(data)
        except FileNotFoundError: self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        if path=="/api/news":
            force=("refresh" in parsed.query)
            news=get_news(force=force)
            self.send_json({"items":news,"count":len(news),
                            "ts":datetime.now(timezone.utc).isoformat(),
                            "claude":bool(ANTHROPIC_API_KEY),"gemini":bool(GEMINI_API_KEY)})
        elif path=="/api/search":
            q=urllib.parse.parse_qs(parsed.query).get("q",[""])[0].strip()
            if not q: self.send_json({"error":"missing q"},code=400); return
            self.send_json({"items":search_deals(q),"query":q})
        elif path=="/api/test-keys":
            # Show discovered Gemini models + smoke-test both APIs
            discovered = list_gemini_models()
            c_text, c_err = call_claude("Reply with exactly: {\"ok\":true}")
            g_text, g_err = call_gemini("Reply with exactly: {\"ok\":true}")
            self.send_json({
                "claude_key_set":    bool(ANTHROPIC_API_KEY),
                "gemini_key_set":    bool(GEMINI_API_KEY),
                "gemini_models_found": [f"{v}/{m}" for v,m in discovered[:8]],
                "claude_ok":  bool(c_text),
                "claude_err": c_err,
                "gemini_ok":  bool(g_text),
                "gemini_err": g_err,
            })
        elif path in ("/","/index.html"):
            self.send_file("index.html","text/html; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path=="/api/analyze":
            try:
                length = int(self.headers.get("Content-Length",0))
                body   = json.loads(self.rfile.read(length).decode())
                result = analyze_deal(
                    title       = body.get("title",""),
                    description = body.get("description",""),
                    sector      = body.get("sector","general"),
                    deal_value  = body.get("dealValue","")
                )
                self.send_json(result)
            except Exception as e:
                self.send_json({"error":str(e)},code=500)
        else:
            self.send_error(404)

if __name__=="__main__":
    print(f"[DealFlow] http://0.0.0.0:{PORT}  Claude={'on' if ANTHROPIC_API_KEY else 'off'}  Gemini={'on' if GEMINI_API_KEY else 'off'}")
    threading.Thread(target=get_news,daemon=True).start()
    HTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
