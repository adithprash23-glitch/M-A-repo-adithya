"""
Mergeon Financial Intelligence – Vercel Serverless Handler
All /api/* routes handled here. Uses a curated 40-stock universe
that batch-downloads in ~6-8 s, safely within Vercel's limit.
"""

import json, os, math, re, time, threading
import html as _html
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from email.utils import parsedate_to_datetime
import urllib.request, urllib.error

# ── Dependencies ────────────────────────────────────────────────────────────────
try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False

try:
    import pandas as pd
    import numpy as np
    PANDAS_OK = True
except ImportError:
    PANDAS_OK = False

# ── Curated universe (fast-load ~40 stocks, ~6 s batch download) ────────────────
STOCK_UNIVERSE = {
    "US": {
        "Technology":  ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMD", "AMZN"],
        "Finance":     ["JPM", "V", "MA", "GS", "BAC"],
        "Healthcare":  ["UNH", "JNJ", "LLY", "PFE", "ABBV"],
        "Energy":      ["XOM", "CVX", "SLB", "COP"],
        "Consumer":    ["TSLA", "WMT", "NKE", "MCD"],
        "Industrial":  ["CAT", "GE", "HON"],
    },
    "India": {
        "IT":             ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS"],
        "Banking":        ["HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "SBIN.NS"],
        "FMCG":           ["ITC.NS", "HINDUNILVR.NS", "NESTLEIND.NS"],
        "Pharma":         ["SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS"],
        "Auto":           ["MARUTI.NS", "TATAMOTORS.NS"],
        "Infrastructure": ["RELIANCE.NS", "BHARTIARTL.NS", "NTPC.NS", "ADANIPORTS.NS"],
    }
}

# ── Module-level cache (persists across warm Vercel invocations) ─────────────────
_cache      = {"stocks": None, "ts": 0, "loading": False}
_fund       = {}          # ticker → info dict
_fund_ts    = {}          # ticker → unix ts
_lock       = threading.Lock()
_news_cache = {}
_news_ts    = {}

PRICE_TTL = 900    # 15 min
FUND_TTL  = 86400  # 24 hr
NEWS_TTL  = 1200   # 20 min

# ── News feeds ──────────────────────────────────────────────────────────────────
NEWS_FEEDS = {
    "markets":    [("Reuters Business",  "https://feeds.reuters.com/reuters/businessNews"),
                   ("MarketWatch",       "https://feeds.marketwatch.com/marketwatch/topstories/"),
                   ("CNBC Markets",      "https://www.cnbc.com/id/20910258/device/rss/rss.html")],
    "technology": [("Reuters Tech",      "https://feeds.reuters.com/reuters/technologyNews"),
                   ("CNBC Tech",         "https://www.cnbc.com/id/19854910/device/rss/rss.html")],
    "energy":     [("Reuters Commodities","https://feeds.reuters.com/reuters/commoditiesNews"),
                   ("CNBC Energy",        "https://www.cnbc.com/id/19836768/device/rss/rss.html")],
    "finance":    [("Reuters Finance",   "https://feeds.reuters.com/reuters/financialservicesNews"),
                   ("CNBC Finance",      "https://www.cnbc.com/id/10000664/device/rss/rss.html")],
    "healthcare": [("Reuters Health",    "https://feeds.reuters.com/reuters/healthNews")],
    "realestate": [("CNBC Real Estate",  "https://www.cnbc.com/id/10000115/device/rss/rss.html")],
    "india":      [("Economic Times",    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
                   ("ET Stocks",         "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms")],
}

_HIGH_IMPACT = {"earnings","profit","revenue","guidance","forecast","merger","acquisition",
                "ipo","fda","approval","federal reserve","rate hike","rate cut","inflation",
                "gdp","recession","opec","sanctions","tariff","bankruptcy","dividend",
                "buyback","quarterly","beat","miss","upgrade","downgrade","price target"}
_SPEC_KW    = {"rumor","allegedly","could potentially","speculation","opinion:"}
_COMPANY_KW = {
    "AAPL":["apple","iphone","tim cook"],"MSFT":["microsoft","azure","copilot"],
    "GOOGL":["google","alphabet","youtube"],"NVDA":["nvidia","jensen huang","blackwell"],
    "META":["meta","facebook","instagram","zuckerberg"],"AMD":["amd","lisa su"],
    "AMZN":["amazon","aws","andy jassy"],"TSLA":["tesla","elon musk"],
    "JPM":["jpmorgan","jamie dimon"],"GS":["goldman sachs"],
    "XOM":["exxonmobil","exxon"],"CVX":["chevron"],
    "JNJ":["johnson & johnson","j&j"],"UNH":["unitedhealth"],
    "LLY":["eli lilly","mounjaro"],"PFE":["pfizer"],"ABBV":["abbvie"],
    "TCS.NS":["tcs","tata consultancy"],"INFY.NS":["infosys"],
    "WIPRO.NS":["wipro"],"HCLTECH.NS":["hcl tech"],
    "HDFCBANK.NS":["hdfc bank"],"ICICIBANK.NS":["icici bank"],
    "KOTAKBANK.NS":["kotak mahindra"],"SBIN.NS":["state bank of india","sbi"],
    "RELIANCE.NS":["reliance industries","jio","mukesh ambani"],
    "MARUTI.NS":["maruti","suzuki"],"TATAMOTORS.NS":["tata motors"],
    "BHARTIARTL.NS":["airtel","bharti"],"SUNPHARMA.NS":["sun pharma"],
}

# ── Helpers ─────────────────────────────────────────────────────────────────────
def safe_float(val, default=0.0):
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default

# ── Technical analysis ───────────────────────────────────────────────────────────
def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    delta = closes.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-10)
    return safe_float((100 - (100 / (1 + rs))).iloc[-1], 50.0)

def calc_macd(closes):
    if len(closes) < 26: return 0.0, 0.0
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    return safe_float(macd.iloc[-1]), safe_float(sig.iloc[-1])

def sma(closes, p):
    if len(closes) < p: return safe_float(closes.iloc[-1])
    return safe_float(closes.rolling(p).mean().iloc[-1])

def tech_score(hist):
    if hist is None or len(hist) < 20: return 50, {}
    closes = hist["Close"].astype(float)
    price  = safe_float(closes.iloc[-1])
    rsi    = calc_rsi(closes)
    macd, sig = calc_macd(closes)
    s20    = sma(closes, 20)
    s50    = sma(closes, 50) if len(closes) >= 50 else s20
    ret5   = ((price - safe_float(closes.iloc[-6])) / safe_float(closes.iloc[-6]) * 100
              if len(closes) >= 6 else 0.0)

    rsi_pts  = 30 if rsi < 40 else 26 if rsi < 30 else 22 if rsi < 50 else 18 if rsi < 65 else 12 if rsi < 72 else 6
    macd_pts = 25 if macd > 0 and macd > sig else 17 if macd > sig else 11 if macd > 0 else 4
    ma_pts   = 25 if price > s20 > s50 else 17 if price > s20 else 11 if price > s50 else 4
    mom_pts  = 20 if ret5 > 5 else 16 if ret5 > 2 else 12 if ret5 > 0 else 8 if ret5 > -2 else 3
    total    = rsi_pts + macd_pts + ma_pts + mom_pts

    return total, {"rsi": round(rsi,1), "macd": round(macd,4), "macd_signal": round(sig,4),
                   "sma20": round(s20,2), "sma50": round(s50,2), "five_day_return": round(ret5,2),
                   "ma_pts": ma_pts, "macd_pts": macd_pts}

def fund_score(info):
    if not info or len(info) < 5: return 50, {}
    pe  = info.get("trailingPE") or info.get("forwardPE")
    rg  = info.get("revenueGrowth")
    de  = info.get("debtToEquity")
    mg  = info.get("profitMargins")

    pe_pts  = (30 if pe and pe < 12 else 25 if pe and pe < 20 else 18 if pe and pe < 30
               else 10 if pe and pe < 50 else 4)
    rev_pts = (25 if rg and rg > 0.25 else 21 if rg and rg > 0.15 else 17 if rg and rg > 0.08
               else 11 if rg and rg > 0 else 12 if rg is None else 4)
    de_pts  = (25 if de and de < 20 else 20 if de and de < 60 else 14 if de and de < 120
               else 8 if de and de < 250 else 14 if de is None else 3)
    mg_pts  = (20 if mg and mg > 0.25 else 16 if mg and mg > 0.15 else 11 if mg and mg > 0.08
               else 6 if mg and mg > 0 else 8 if mg is None else 0)
    total   = pe_pts + rev_pts + de_pts + mg_pts

    return total, {
        "pe_ratio":           round(pe, 1) if pe else None,
        "revenue_growth_pct": round(rg*100, 1) if rg else None,
        "debt_equity":        round(de/100, 2) if de else None,
        "profit_margin_pct":  round(mg*100, 1) if mg else None,
        "market_cap":         info.get("marketCap"),
        "long_name":          info.get("longName") or info.get("shortName",""),
        "currency":           info.get("currency","USD"),
    }

def gen_reason(td, fd):
    parts = []
    rsi, ma_pts = td.get("rsi",50), td.get("ma_pts",0)
    macd, sig   = td.get("macd",0), td.get("macd_signal",0)
    ret5        = td.get("five_day_return",0)
    pe, rg, mg  = fd.get("pe_ratio"), fd.get("revenue_growth_pct"), fd.get("profit_margin_pct")

    if ma_pts >= 25:   parts.append("above SMA20 & SMA50 (uptrend)")
    elif ma_pts >= 17: parts.append("above SMA20 (short-term bullish)")
    elif ma_pts <= 4:  parts.append("below key MAs (bearish)")
    if rsi < 32:       parts.append(f"RSI {rsi:.0f} oversold")
    elif rsi > 68:     parts.append(f"RSI {rsi:.0f} overbought")
    else:              parts.append(f"RSI {rsi:.0f}")
    if macd > sig and macd > 0: parts.append("MACD bullish")
    elif macd < sig and macd < 0: parts.append("MACD bearish")
    if ret5 > 3:  parts.append(f"+{ret5:.1f}% 5d momentum")
    elif ret5 < -3: parts.append(f"{ret5:.1f}% 5d weakness")
    if pe and pe < 15:  parts.append(f"value P/E {pe:.0f}")
    elif pe and pe > 40: parts.append(f"premium P/E {pe:.0f}")
    if rg and rg > 15:  parts.append(f"{rg:.0f}% rev growth")
    if mg and mg > 20:  parts.append(f"{mg:.0f}% margin")
    return " · ".join(parts[:5]) + "." if parts else "Neutral."

def gen_signal(td, fd):
    tags = []
    rsi, ret5 = td.get("rsi",50), td.get("five_day_return",0)
    macd, sig  = td.get("macd",0), td.get("macd_signal",0)
    ma_pts     = td.get("ma_pts",0)
    pe, rg     = fd.get("pe_ratio"), fd.get("revenue_growth_pct")
    if rsi < 32: tags.append("Oversold")
    elif rsi > 68: tags.append("Overbought")
    if macd > sig and macd > 0: tags.append("MACD Bullish")
    elif macd < sig and macd < 0: tags.append("MACD Bearish")
    if ma_pts >= 25: tags.append("Strong Uptrend")
    elif ma_pts <= 4: tags.append("Below Key MAs")
    if pe and pe < 14: tags.append("Cheap Valuation")
    if rg and rg > 18: tags.append("High Growth")
    if ret5 > 4: tags.append("Strong Momentum")
    elif ret5 < -4: tags.append("Selling Pressure")
    return " · ".join(tags[:3]) if tags else "Neutral"

# ── Data fetch ───────────────────────────────────────────────────────────────────
def fetch_stocks():
    if not YFINANCE_OK or not PANDAS_OK:
        return []

    all_tickers, meta = [], {}
    for region, industries in STOCK_UNIVERSE.items():
        for industry, tickers in industries.items():
            for t in tickers:
                all_tickers.append(t); meta[t] = {"region": region, "industry": industry}

    try:
        raw = yf.download(all_tickers, period="90d", interval="1d",
                          group_by="ticker", auto_adjust=True, progress=False, threads=True)
    except Exception as e:
        print(f"yf.download: {e}"); return []

    results = []
    n = len(all_tickers)
    for ticker in all_tickers:
        try:
            if n == 1:
                hist = raw
            else:
                lvl0 = raw.columns.get_level_values(0)
                if ticker not in lvl0: continue
                hist = raw[ticker]
            hist = hist.dropna(how="all")
            if "Close" not in hist.columns or len(hist) < 5: continue

            closes  = hist["Close"].astype(float)
            price   = safe_float(closes.iloc[-1])
            prev    = safe_float(closes.iloc[-2]) if len(closes) >= 2 else price
            chg_pct = (price - prev) / prev * 100 if prev else 0.0

            ts, td  = tech_score(hist)
            fi       = _fund.get(ticker, {})
            fs, fd  = fund_score(fi)
            combined = round((ts + fs) / 2)
            signal   = gen_signal(td, fd)
            reason   = gen_reason(td, fd)
            name     = fd.get("long_name") or fi.get("shortName","") or ticker.replace(".NS","")
            currency = fd.get("currency") or ("INR" if ".NS" in ticker else "USD")
            spark    = [round(safe_float(v),2) for v in closes.tail(14).tolist()]

            results.append({
                "ticker": ticker, "name": name,
                "region": meta[ticker]["region"], "industry": meta[ticker]["industry"],
                "currency": currency,
                "current_price": round(price,2), "prev_price": round(prev,2),
                "change_pct": round(chg_pct,2), "change_abs": round(price-prev,2),
                "volume": int(safe_float(hist["Volume"].iloc[-1])) if "Volume" in hist.columns else 0,
                "market_cap": fd.get("market_cap"),
                "technical_score": ts, "fundamental_score": fs, "combined_score": combined,
                "signal": signal, "reason": reason,
                "tech_details": td, "fund_details": fd,
                "sparkline": spark,
                "high_90d": round(safe_float(closes.max()),2),
                "low_90d":  round(safe_float(closes.min()),2),
                "last_updated": datetime.now().isoformat(),
            })
        except Exception:
            pass
    return results

def ensure_stocks():
    """Return cached stocks or fetch fresh. Thread-safe."""
    now = time.time()
    with _lock:
        if _cache["stocks"] and now - _cache["ts"] < PRICE_TTL:
            return _cache["stocks"], False
        if _cache["loading"]:
            return _cache["stocks"] or [], True
        _cache["loading"] = True

    try:
        stocks = fetch_stocks()
        with _lock:
            _cache["stocks"]  = stocks
            _cache["ts"]      = time.time()
            _cache["loading"] = False
        return stocks, False
    except Exception as e:
        print(f"ensure_stocks: {e}")
        with _lock:
            _cache["loading"] = False
        return _cache["stocks"] or [], False

# ── News ─────────────────────────────────────────────────────────────────────────
def _clean(s):
    s = re.sub(r'<!\[CDATA\[|\]\]>', '', s or '')
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return _html.unescape(s).strip()

def _pub_ts(pub):
    try: return parsedate_to_datetime(pub).timestamp()
    except: return time.time() - 3600

def _score_article(title, desc):
    text  = (title+" "+desc).lower()
    score = 30
    for kws in _COMPANY_KW.values():
        if any(k in text for k in kws): score += 30; break
    score += min(sum(1 for k in _HIGH_IMPACT if k in text)*6, 24)
    if any(c.isdigit() for c in title): score += 5
    if any(k in text for k in _SPEC_KW): score -= 20
    return max(0, min(score, 95))

def _parse_rss(url, source):
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0 (Mergeon/1.0)"})
        with urllib.request.urlopen(req, timeout=8) as r:
            root = ET.fromstring(r.read())
        ch = root.find("channel")
        if ch is None: return []
        items = []
        for item in ch.findall("item")[:20]:
            t = _clean(item.findtext("title",""))
            l = _clean(item.findtext("link",""))
            d = _clean(item.findtext("description",""))[:280]
            p = _clean(item.findtext("pubDate",""))
            if t and l: items.append({"title":t,"link":l,"desc":d,"pub":p,"source":source})
        return items
    except Exception as e:
        print(f"RSS {source}: {e}"); return []

def get_news(cat="markets"):
    now = time.time()
    if now - _news_ts.get(cat, 0) < NEWS_TTL:
        return _news_cache.get(cat, [])

    feeds = NEWS_FEEDS.get(cat, [])
    raw   = []
    for src, url in feeds:
        raw.extend(_parse_rss(url, src))

    seen, out = set(), []
    for item in raw:
        key   = item["title"].lower()[:55]
        if key in seen: continue
        seen.add(key)
        ts    = _pub_ts(item["pub"])
        age_h = (now - ts) / 3600
        if age_h > 48: continue
        score = _score_article(item["title"], item["desc"])
        if age_h < 1: score += 10
        elif age_h < 4: score += 5
        elif age_h > 16: score -= 5
        tickers = [t for t, kws in _COMPANY_KW.items() if any(k in (item["title"]+" "+item["desc"]).lower() for k in kws)]
        out.append({**item, "ts": ts, "age_h": round(age_h,1),
                    "score": min(100, score), "tickers": tickers[:4]})

    out = sorted([a for a in out if a["score"] >= 45], key=lambda x: (-x["score"], x["age_h"]))[:28]
    _news_cache[cat] = out
    _news_ts[cat]    = now
    return out

# ── AI ───────────────────────────────────────────────────────────────────────────
GROQ_MODELS = ["llama-3.3-70b-versatile","llama3-70b-8192","mixtral-8x7b-32768"]

def _groq(msgs, system=None, max_tokens=1200):
    key = os.environ.get("GROQ_API_KEY","")
    if not key: raise ValueError("No GROQ_API_KEY")
    full = ([{"role":"system","content":system}] if system else []) + msgs
    last = None
    for i, model in enumerate(GROQ_MODELS):
        try:
            body = json.dumps({"model":model,"max_tokens":max_tokens,"messages":full,"temperature":0.3}).encode()
            req  = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=body,
                   headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"]
        except Exception as e:
            last = e
            if "429" in str(e) or "rate" in str(e).lower():
                if i < len(GROQ_MODELS)-1: time.sleep(1)
                continue
            raise
    raise Exception(f"rate_limit: {last}")

def _gemini(msgs, system=None, max_tokens=1200):
    key = os.environ.get("GEMINI_API_KEY","")
    if not key: raise ValueError("No GEMINI_API_KEY")
    parts = []
    if system: parts.append({"text": system+"\n\n"})
    for m in msgs: parts.append({"text": f"[{m['role'].upper()}]: {m['content']}\n"})
    body = json.dumps({"contents":[{"parts":parts}],"generationConfig":{"temperature":0.3,"maxOutputTokens":max_tokens}}).encode()
    req  = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
        data=body, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]

def ai_request(msgs, system=None, max_tokens=1200):
    gq = os.environ.get("GROQ_API_KEY","")
    gm = os.environ.get("GEMINI_API_KEY","")
    if not gq and not gm: raise ValueError("Set GROQ_API_KEY or GEMINI_API_KEY")
    if gq:
        try: return _groq(msgs, system, max_tokens)
        except Exception as e:
            if "rate_limit" not in str(e) and "429" not in str(e): raise
    if gm:
        try: return _gemini(msgs, system, max_tokens)
        except Exception as e:
            if "429" in str(e): raise Exception("rate_limit")
            raise
    raise Exception("rate_limit")

def chat_with_ai(message, history, context):
    system = """You are Mergeon AI, a professional financial research assistant. Be concise, data-driven, and professional. Use actual numbers from context when available. Do not give explicit buy/sell advice. Do not reveal which AI provider powers you."""
    ctx = ""
    if context:
        top5 = context.get("top5",[])
        ctx  = f"\n\nMarket snapshot: {context.get('total',0)} stocks, {context.get('gainers',0)} gainers, {context.get('losers',0)} losers."
        if top5:
            ctx += " Top 5: " + ", ".join(f"{s['ticker']}(score {s['score']}, {s['change']:+.1f}%)" for s in top5) + "."
    msgs = list(history or [])
    msgs.append({"role":"user","content": message+ctx})
    return ai_request(msgs, system=system, max_tokens=500)

def analyze_top_picks(stocks):
    if not stocks: return {"error":"No stock data loaded yet."}
    picks = []
    for s in sorted(stocks, key=lambda x: x["combined_score"], reverse=True)[:10]:
        picks.append({"ticker":s["ticker"],"name":s["name"],"industry":s["industry"],
                      "region":s["region"],"change_pct":s["change_pct"],
                      "combined_score":s["combined_score"],"technical_score":s["technical_score"],
                      "fundamental_score":s["fundamental_score"],
                      "rsi":s["tech_details"].get("rsi"),"five_day_return":s["tech_details"].get("five_day_return"),
                      "pe_ratio":s["fund_details"].get("pe_ratio"),
                      "revenue_growth_pct":s["fund_details"].get("revenue_growth_pct"),
                      "profit_margin_pct":s["fund_details"].get("profit_margin_pct"),
                      "signal":s["signal"],"reason":s.get("reason","")})

    prompt = f"""You are a senior equity research analyst. Our quant screener flagged these stocks today:

{json.dumps(picks, indent=2)}

For each stock give:
1. thesis — one crisp sentence on WHY it scored well
2. key_risk — the single biggest risk
3. conviction — HIGH, MEDIUM, or LOW

Then write 2-3 sentences of macro_themes across these picks.

Respond ONLY with valid JSON, no markdown:
{{"picks":[{{"ticker":"...","thesis":"...","key_risk":"...","conviction":"HIGH|MEDIUM|LOW"}}],"macro_themes":"..."}}"""

    try:
        text = ai_request([{"role":"user","content":prompt}], max_tokens=1600)
        text = text.strip()
        s, e = text.find("{"), text.rfind("}")+1
        return json.loads(text[s:e]) if s >= 0 and e > s else {"error":"Could not parse AI response."}
    except Exception as ex:
        err = str(ex)
        if "rate_limit" in err or "429" in err:
            return {"error":"AI rate limit reached. Please wait 30 seconds and try again."}
        return {"error": str(ex)}

# ── HTTP Handler ─────────────────────────────────────────────────────────────────
class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, status=200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors(); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        p, qs  = parsed.path, parse_qs(parsed.query)

        if p == "/api/stocks":
            stocks, loading = ensure_stocks()
            ts = _cache.get("ts", 0)
            self._json({"stocks": stocks, "count": len(stocks),
                        "loading": loading,
                        "last_updated": datetime.fromtimestamp(ts).isoformat() if ts else None})

        elif p == "/api/top-picks":
            stocks, _ = ensure_stocks()
            self._json({"picks": sorted(stocks, key=lambda x: x["combined_score"], reverse=True)[:20]})

        elif p == "/api/refresh":
            with _lock:
                _cache["stocks"] = None; _cache["ts"] = 0
            threading.Thread(target=ensure_stocks, daemon=True).start()
            self._json({"status": "refresh_started"})

        elif p == "/api/news":
            cat = qs.get("category", ["markets"])[0]
            if cat not in NEWS_FEEDS: cat = "markets"
            self._json({"articles": get_news(cat), "category": cat})

        elif p == "/api/status":
            self._json({"status":"ok","stocks_loaded":len(_cache.get("stocks") or []),
                        "yfinance":YFINANCE_OK,"pandas":PANDAS_OK})
        else:
            self.send_error(404)

    def do_POST(self):
        p      = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        if p == "/api/analyze":
            stocks, _ = ensure_stocks()
            self._json(analyze_top_picks(stocks))

        elif p == "/api/chat":
            message = body.get("message","").strip()
            history = body.get("history", [])
            context = body.get("context")
            if not message:
                self._json({"error":"No message."}); return
            try:
                self._json({"reply": chat_with_ai(message, history, context)})
            except Exception as e:
                err = str(e)
                if "rate_limit" in err or "429" in err:
                    self._json({"reply":"I'm at capacity — please wait 30 seconds and try again."})
                else:
                    self._json({"reply":"Unable to respond right now. Please try again."})
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        pass  # suppress Vercel noise
