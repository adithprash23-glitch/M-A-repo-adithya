#!/usr/bin/env python3
"""
Mergeon Financial Intelligence - Stock Analysis Server
Tracks 90 US & Indian stocks with technical + fundamental scoring
and AI-powered top pick analysis via Claude
"""

import json
import threading
import time
import os
import math
import re
import html as _html
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from email.utils import parsedate_to_datetime
import urllib.request
import urllib.error

try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False
    print("ERROR: yfinance not installed. Run: pip install yfinance pandas numpy")

try:
    import pandas as pd
    import numpy as np
    PANDAS_OK = True
except ImportError:
    PANDAS_OK = False
    print("ERROR: pandas/numpy not installed. Run: pip install yfinance pandas numpy")


# ─── Stock Universe ────────────────────────────────────────────────────────────

STOCK_UNIVERSE = {
    "US": {
        "Technology":  ["AAPL", "MSFT", "GOOGL", "NVDA", "META", "AMD", "ORCL", "CRM"],
        "Healthcare":  ["JNJ", "UNH", "LLY", "ABBV", "MRK", "PFE", "ABT", "AMGN"],
        "Finance":     ["JPM", "V", "MA", "BAC", "WFC", "GS", "BLK", "AXP"],
        "Energy":      ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "VLO", "OXY"],
        "Consumer":    ["AMZN", "TSLA", "HD", "MCD", "NKE", "WMT", "COST", "PG"],
        "Industrial":  ["GE", "CAT", "HON", "BA", "UPS", "RTX", "LMT", "DE"],
    },
    "India": {
        "IT":             ["INFY.NS", "TCS.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS", "LTIM.NS"],
        "Banking":        ["HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS", "SBIN.NS", "INDUSINDBK.NS"],
        "FMCG":           ["HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS", "MARICO.NS"],
        "Pharma":         ["SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "AUROPHARMA.NS", "LUPIN.NS"],
        "Auto":           ["MARUTI.NS", "TATAMOTORS.NS", "M&M.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS", "EICHERMOT.NS"],
        "Infrastructure": ["ADANIPORTS.NS", "POWERGRID.NS", "NTPC.NS", "BHARTIARTL.NS", "RELIANCE.NS", "ONGC.NS"],
    }
}

# ─── News Feeds ────────────────────────────────────────────────────────────────

NEWS_TTL = 1200  # 20 minutes

NEWS_FEEDS = {
    "markets": [
        ("Reuters Business",  "https://feeds.reuters.com/reuters/businessNews"),
        ("MarketWatch",       "https://feeds.marketwatch.com/marketwatch/topstories/"),
        ("CNBC Markets",      "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ],
    "technology": [
        ("Reuters Tech",      "https://feeds.reuters.com/reuters/technologyNews"),
        ("CNBC Tech",         "https://www.cnbc.com/id/19854910/device/rss/rss.html"),
    ],
    "energy": [
        ("Reuters Commodities","https://feeds.reuters.com/reuters/commoditiesNews"),
        ("CNBC Energy",        "https://www.cnbc.com/id/19836768/device/rss/rss.html"),
    ],
    "finance": [
        ("Reuters Finance",   "https://feeds.reuters.com/reuters/financialservicesNews"),
        ("CNBC Finance",      "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ],
    "healthcare": [
        ("Reuters Health",    "https://feeds.reuters.com/reuters/healthNews"),
        ("CNBC Health",       "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ],
    "realestate": [
        ("CNBC Real Estate",  "https://www.cnbc.com/id/10000115/device/rss/rss.html"),
        ("Reuters Business",  "https://feeds.reuters.com/reuters/businessNews"),
    ],
    "india": [
        ("Economic Times",    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
        ("ET Stocks",         "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
    ],
}

_HIGH_IMPACT_KW = {
    "earnings","profit","revenue","guidance","forecast","merger","acquisition",
    "ipo","fda","approval","federal reserve","rate hike","rate cut","inflation",
    "gdp","recession","opec","sanctions","tariff","bankruptcy","dividend",
    "buyback","quarterly","beat expectations","miss","upgrade","downgrade",
    "price target","restructuring","layoffs","expansion"
}
_SPEC_KW = {"rumor","allegedly","could potentially","speculation","opinion:","commentary:"}

_COMPANY_KW = {
    "AAPL":          ["apple","iphone","ipad","tim cook","app store"],
    "MSFT":          ["microsoft","azure","windows","copilot","satya nadella","openai"],
    "GOOGL":         ["google","alphabet","youtube","android","gemini","waymo"],
    "NVDA":          ["nvidia","cuda","gpu","jensen huang","blackwell","hopper"],
    "META":          ["meta","facebook","instagram","whatsapp","zuckerberg","threads"],
    "AMD":           ["amd","ryzen","radeon","epyc","lisa su"],
    "ORCL":          ["oracle","larry ellison","oci"],
    "CRM":           ["salesforce","marc benioff","slack"],
    "TSLA":          ["tesla","elon musk","cybertruck","supercharger"],
    "AMZN":          ["amazon","aws","andy jassy","prime","whole foods"],
    "JPM":           ["jpmorgan","jp morgan","jamie dimon"],
    "GS":            ["goldman sachs","goldman"],
    "BAC":           ["bank of america","bofa"],
    "WFC":           ["wells fargo"],
    "V":             ["visa"],
    "MA":            ["mastercard"],
    "XOM":           ["exxonmobil","exxon"],
    "CVX":           ["chevron"],
    "COP":           ["conocophillips"],
    "JNJ":           ["johnson & johnson","j&j"],
    "UNH":           ["unitedhealth","united health"],
    "LLY":           ["eli lilly","mounjaro","zepbound"],
    "PFE":           ["pfizer"],
    "ABBV":          ["abbvie","humira"],
    "TCS.NS":        ["tcs","tata consultancy"],
    "INFY.NS":       ["infosys"],
    "WIPRO.NS":      ["wipro"],
    "HDFCBANK.NS":   ["hdfc bank","hdfc"],
    "ICICIBANK.NS":  ["icici bank","icici"],
    "KOTAKBANK.NS":  ["kotak mahindra","kotak bank"],
    "SBIN.NS":       ["state bank of india","sbi"],
    "RELIANCE.NS":   ["reliance industries","jio","mukesh ambani"],
    "MARUTI.NS":     ["maruti","suzuki"],
    "TATAMOTORS.NS": ["tata motors","jaguar","land rover"],
    "BHARTIARTL.NS": ["airtel","bharti"],
}

_news_cache   = {}   # category -> list of articles
_news_updated = {}   # category -> unix timestamp
_news_lock    = threading.Lock()


def _clean_text(s):
    s = re.sub(r'<!\[CDATA\[|\]\]>', '', s or '')
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return _html.unescape(s).strip()


def _parse_rss(url, source):
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Mergeon/1.0; +https://mergeon.com)"}
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            content = resp.read()
        root = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            return []
        items = []
        for item in channel.findall("item")[:20]:
            title = _clean_text(item.findtext("title",""))
            link  = _clean_text(item.findtext("link",""))
            desc  = _clean_text(item.findtext("description",""))[:300]
            pub   = _clean_text(item.findtext("pubDate",""))
            if title and link:
                items.append({"title":title,"link":link,"desc":desc,"pub":pub,"source":source})
        return items
    except Exception as e:
        print(f"  RSS {source}: {e}")
        return []


def _pub_ts(pub_str):
    try:
        return parsedate_to_datetime(pub_str).timestamp()
    except Exception:
        return time.time() - 3600


def _score_article(title, desc):
    text  = (title + " " + desc).lower()
    score = 30
    for kws in _COMPANY_KW.values():
        if any(k in text for k in kws):
            score += 30
            break
    score += min(sum(1 for k in _HIGH_IMPACT_KW if k in text) * 6, 24)
    if any(c.isdigit() for c in title):
        score += 5
    if any(k in text for k in _SPEC_KW):
        score -= 20
    return max(0, min(score, 95))


def _fetch_news_category(category):
    feeds = NEWS_FEEDS.get(category, [])
    raw = []
    for source, url in feeds:
        raw.extend(_parse_rss(url, source))

    now       = time.time()
    seen      = set()
    scored    = []
    for item in raw:
        key = item["title"].lower()[:55]
        if key in seen:
            continue
        seen.add(key)
        ts    = _pub_ts(item["pub"])
        age_h = (now - ts) / 3600
        if age_h > 48:
            continue
        score = _score_article(item["title"], item["desc"])
        if age_h < 1:   score += 10
        elif age_h < 4: score += 5
        elif age_h > 16: score -= 5
        # find related tickers
        text  = (item["title"] + " " + item["desc"]).lower()
        tickers = [t for t, kws in _COMPANY_KW.items() if any(k in text for k in kws)]
        scored.append({**item, "ts": ts, "age_h": round(age_h,1),
                       "score": min(100, score), "tickers": tickers[:4]})

    out = [a for a in scored if a["score"] >= 45]
    out.sort(key=lambda x: (-x["score"], x["age_h"]))
    return out[:30]


def fetch_all_news():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Refreshing news feeds…")
    for cat in NEWS_FEEDS:
        try:
            arts = _fetch_news_category(cat)
            with _news_lock:
                _news_cache[cat]   = arts
                _news_updated[cat] = time.time()
            print(f"  news/{cat}: {len(arts)} articles")
        except Exception as e:
            print(f"  news/{cat} error: {e}")


def get_news(category="markets"):
    now = time.time()
    with _news_lock:
        if now - _news_updated.get(category, 0) < NEWS_TTL:
            return _news_cache.get(category, [])
    arts = _fetch_news_category(category)
    with _news_lock:
        _news_cache[category]   = arts
        _news_updated[category] = now
    return arts


def _periodic_news_refresh():
    while True:
        time.sleep(NEWS_TTL)
        fetch_all_news()


# ─── Cache ─────────────────────────────────────────────────────────────────────

_cache = {
    "stocks":                None,
    "last_updated":          None,
    "loading":               False,
    "fundamentals":          {},   # ticker -> info dict
    "fundamentals_updated":  {},   # ticker -> unix timestamp
}
_cache_lock   = threading.Lock()
_fund_lock    = threading.Lock()

PRICE_TTL        = 900    # 15 minutes
FUND_TTL         = 86400  # 24 hours
FUND_CACHE_FILE  = os.path.join(os.path.dirname(__file__), "fundamentals_cache.json")


# ─── Technical Analysis ────────────────────────────────────────────────────────

def safe_float(val, default=0.0):
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    delta = closes.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-10)
    rsi   = 100 - (100 / (1 + rs))
    return safe_float(rsi.iloc[-1], 50.0)


def calculate_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow:
        return 0.0, 0.0, 0.0
    ema_f  = closes.ewm(span=fast,   adjust=False).mean()
    ema_s  = closes.ewm(span=slow,   adjust=False).mean()
    macd   = ema_f - ema_s
    sig    = macd.ewm(span=signal, adjust=False).mean()
    hist   = macd - sig
    return safe_float(macd.iloc[-1]), safe_float(sig.iloc[-1]), safe_float(hist.iloc[-1])


def sma(closes, period):
    if len(closes) < period:
        return safe_float(closes.iloc[-1])
    return safe_float(closes.rolling(period).mean().iloc[-1])


def calculate_technical_score(hist):
    """Returns (score 0-100, details dict)"""
    if hist is None or len(hist) < 20:
        return 50, {}

    closes = hist["Close"].astype(float)
    price  = safe_float(closes.iloc[-1])
    prev   = safe_float(closes.iloc[-2]) if len(closes) >= 2 else price

    rsi              = calculate_rsi(closes)
    macd, sig, histo = calculate_macd(closes)
    sma20            = sma(closes, 20)
    sma50            = sma(closes, 50) if len(closes) >= 50 else sma20
    ret5             = ((price - safe_float(closes.iloc[-6])) / safe_float(closes.iloc[-6]) * 100
                        if len(closes) >= 6 else 0.0)

    # RSI (0-30 pts): reward recovery zone 30-45 most
    if   rsi < 30:  rsi_pts = 26
    elif rsi < 40:  rsi_pts = 30
    elif rsi < 50:  rsi_pts = 22
    elif rsi < 65:  rsi_pts = 18
    elif rsi < 72:  rsi_pts = 12
    else:           rsi_pts = 6

    # MACD (0-25 pts)
    if   macd > 0 and macd > sig:  macd_pts = 25
    elif macd > sig:               macd_pts = 17
    elif macd > 0:                 macd_pts = 11
    else:                          macd_pts = 4

    # Moving averages (0-25 pts)
    if   price > sma20 > sma50:    ma_pts = 25
    elif price > sma20:            ma_pts = 17
    elif price > sma50:            ma_pts = 11
    else:                          ma_pts = 4

    # 5-day momentum (0-20 pts)
    if   ret5 >  5:  mom_pts = 20
    elif ret5 >  2:  mom_pts = 16
    elif ret5 >  0:  mom_pts = 12
    elif ret5 > -2:  mom_pts = 8
    else:            mom_pts = 3

    total = rsi_pts + macd_pts + ma_pts + mom_pts  # max 100

    return total, {
        "rsi":            round(rsi, 1),
        "macd":           round(macd, 4),
        "macd_signal":    round(sig,  4),
        "sma20":          round(sma20, 2),
        "sma50":          round(sma50, 2),
        "five_day_return": round(ret5, 2),
        "rsi_pts":        rsi_pts,
        "macd_pts":       macd_pts,
        "ma_pts":         ma_pts,
        "mom_pts":        mom_pts,
    }


# ─── Fundamental Analysis ──────────────────────────────────────────────────────

def calculate_fundamental_score(info):
    """Returns (score 0-100, details dict)"""
    if not info or len(info) < 5:
        return 50, {}

    pe         = info.get("trailingPE") or info.get("forwardPE")
    rev_growth = info.get("revenueGrowth")        # decimal, e.g. 0.15
    debt_eq    = info.get("debtToEquity")          # yfinance %-form, e.g. 50 = 0.5x
    margin     = info.get("profitMargins")         # decimal, e.g. 0.22

    # P/E (0-30 pts)
    if   pe is None or pe < 0: pe_pts = 10
    elif pe < 12:              pe_pts = 30
    elif pe < 20:              pe_pts = 25
    elif pe < 30:              pe_pts = 18
    elif pe < 50:              pe_pts = 10
    else:                      pe_pts = 4

    # Revenue growth (0-25 pts)
    if   rev_growth is None:    rev_pts = 12
    elif rev_growth > 0.25:     rev_pts = 25
    elif rev_growth > 0.15:     rev_pts = 21
    elif rev_growth > 0.08:     rev_pts = 17
    elif rev_growth > 0:        rev_pts = 11
    else:                       rev_pts = 4

    # Debt/equity (0-25 pts) — yfinance stores as %, so 50 = D/E of 0.5
    if   debt_eq is None:   de_pts = 14
    elif debt_eq < 20:      de_pts = 25
    elif debt_eq < 60:      de_pts = 20
    elif debt_eq < 120:     de_pts = 14
    elif debt_eq < 250:     de_pts = 8
    else:                   de_pts = 3

    # Profit margin (0-20 pts)
    if   margin is None:    mg_pts = 8
    elif margin > 0.25:     mg_pts = 20
    elif margin > 0.15:     mg_pts = 16
    elif margin > 0.08:     mg_pts = 11
    elif margin > 0:        mg_pts = 6
    else:                   mg_pts = 0

    total = pe_pts + rev_pts + de_pts + mg_pts  # max 100

    return total, {
        "pe_ratio":          round(pe, 1)                     if pe          else None,
        "revenue_growth_pct": round(rev_growth * 100, 1)      if rev_growth  else None,
        "debt_equity":       round(debt_eq / 100, 2)          if debt_eq     else None,
        "profit_margin_pct": round(margin * 100, 1)           if margin      else None,
        "market_cap":        info.get("marketCap"),
        "long_name":         info.get("longName") or info.get("shortName", ""),
        "currency":          info.get("currency", "USD"),
        "pe_pts":  pe_pts,
        "rev_pts": rev_pts,
        "de_pts":  de_pts,
        "mg_pts":  mg_pts,
    }


def generate_reason(tech_det, fund_det, combined_score):
    """1-2 sentence plain-English explanation of why this stock scored as it did."""
    parts = []
    rsi      = tech_det.get("rsi", 50)
    ma_pts   = tech_det.get("ma_pts", 0)
    macd     = tech_det.get("macd", 0)
    macd_sig = tech_det.get("macd_signal", 0)
    ret5     = tech_det.get("five_day_return", 0)
    pe       = fund_det.get("pe_ratio")
    rg       = fund_det.get("revenue_growth_pct")
    margin   = fund_det.get("profit_margin_pct")
    de       = fund_det.get("debt_equity")

    if ma_pts >= 25:   parts.append("price above SMA20 & SMA50 (strong uptrend)")
    elif ma_pts >= 17: parts.append("price above SMA20 (short-term uptrend)")
    elif ma_pts <= 4:  parts.append("trading below key moving averages")

    if rsi < 32:       parts.append(f"RSI {rsi:.0f} — oversold territory")
    elif rsi > 68:     parts.append(f"RSI {rsi:.0f} — overbought")
    else:              parts.append(f"RSI {rsi:.0f} (neutral)")

    if macd > macd_sig and macd > 0: parts.append("MACD bullish crossover")
    elif macd < macd_sig and macd < 0: parts.append("MACD bearish")

    if ret5 > 3:  parts.append(f"+{ret5:.1f}% 5-day momentum")
    elif ret5 < -3: parts.append(f"{ret5:.1f}% 5-day weakness")

    if pe and pe < 15:  parts.append(f"value-priced at P/E {pe:.0f}")
    elif pe and pe > 40: parts.append(f"premium valuation P/E {pe:.0f}")

    if rg and rg > 15:  parts.append(f"{rg:.0f}% revenue growth")
    if margin and margin > 20: parts.append(f"{margin:.0f}% profit margin")
    if de is not None and de < 0.3: parts.append("low debt")

    if not parts:
        return "Neutral signals across technical and fundamental factors."
    return " · ".join(parts[:5]) + "."


def generate_signal(tech_details, fund_details):
    """Human-readable summary of the dominant signal"""
    tags = []

    rsi      = tech_details.get("rsi", 50)
    ret5     = tech_details.get("five_day_return", 0)
    macd     = tech_details.get("macd", 0)
    macd_sig = tech_details.get("macd_signal", 0)
    ma_pts   = tech_details.get("ma_pts", 0)

    pe       = fund_details.get("pe_ratio")
    rg       = fund_details.get("revenue_growth_pct")
    margin   = fund_details.get("profit_margin_pct")

    if rsi < 32:                    tags.append("Oversold")
    elif rsi > 68:                  tags.append("Overbought")

    if macd > macd_sig and macd > 0: tags.append("MACD Bullish")
    elif macd < macd_sig and macd < 0: tags.append("MACD Bearish")

    if ma_pts >= 25:                tags.append("Strong Uptrend")
    elif ma_pts <= 4:               tags.append("Below Key MAs")

    if pe and pe < 14:              tags.append("Cheap Valuation")
    elif pe and pe > 45:            tags.append("Expensive")

    if rg and rg > 18:              tags.append("High Growth")
    if ret5 > 4:                    tags.append("Strong Momentum")
    elif ret5 < -4:                 tags.append("Selling Pressure")

    return " · ".join(tags[:3]) if tags else "Neutral"


# ─── Data Fetching ─────────────────────────────────────────────────────────────

def load_fundamentals_from_disk():
    try:
        if os.path.exists(FUND_CACHE_FILE):
            with open(FUND_CACHE_FILE, "r") as f:
                data = json.load(f)
            with _fund_lock:
                for ticker, item in data.items():
                    _cache["fundamentals"][ticker]         = item.get("data", {})
                    _cache["fundamentals_updated"][ticker] = item.get("ts", 0)
            print(f"Loaded fundamentals cache for {len(data)} tickers from disk.")
    except Exception as e:
        print(f"Could not load fundamentals cache: {e}")


def save_fundamentals_to_disk():
    try:
        with _fund_lock:
            out = {
                t: {"data": _cache["fundamentals"].get(t, {}),
                    "ts":   _cache["fundamentals_updated"].get(t, 0)}
                for t in _cache["fundamentals"]
            }
        with open(FUND_CACHE_FILE, "w") as f:
            json.dump(out, f)
    except Exception as e:
        print(f"Could not save fundamentals cache: {e}")


def fetch_fundamentals_one(ticker):
    """Fetch & cache info dict for a single ticker. Returns cached if fresh."""
    now = time.time()
    with _fund_lock:
        last = _cache["fundamentals_updated"].get(ticker, 0)
        if now - last < FUND_TTL and ticker in _cache["fundamentals"]:
            return _cache["fundamentals"][ticker]

    try:
        info = yf.Ticker(ticker).info
        if info and isinstance(info, dict) and info.get("regularMarketPrice"):
            with _fund_lock:
                _cache["fundamentals"][ticker]         = info
                _cache["fundamentals_updated"][ticker] = now
            return info
    except Exception:
        pass
    with _fund_lock:
        return _cache["fundamentals"].get(ticker, {})


def _extract_hist(raw, ticker, n_tickers):
    """Safely pull one ticker's OHLCV DataFrame from a yf.download() result."""
    try:
        if n_tickers == 1:
            df = raw
        else:
            lvl0 = raw.columns.get_level_values(0)
            if ticker not in lvl0:
                return None
            df = raw[ticker]
        df = df.dropna(how="all")
        if "Close" not in df.columns or len(df) < 5:
            return None
        return df
    except Exception:
        return None


def fetch_all_stocks():
    """Batch-download prices, score every stock, return list of dicts."""
    if not YFINANCE_OK or not PANDAS_OK:
        return []

    # Build flat ticker list + metadata lookup
    all_tickers = []
    meta        = {}
    for region, industries in STOCK_UNIVERSE.items():
        for industry, tickers in industries.items():
            for t in tickers:
                all_tickers.append(t)
                meta[t] = {"region": region, "industry": industry}

    n = len(all_tickers)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching prices for {n} tickers …")

    try:
        raw = yf.download(
            all_tickers,
            period="90d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"yf.download error: {e}")
        return []

    results = []
    for ticker in all_tickers:
        try:
            hist = _extract_hist(raw, ticker, n)
            if hist is None:
                continue

            closes  = hist["Close"].astype(float)
            price   = safe_float(closes.iloc[-1])
            prev    = safe_float(closes.iloc[-2]) if len(closes) >= 2 else price
            chg_pct = ((price - prev) / prev * 100) if prev else 0.0

            tech_score, tech_det = calculate_technical_score(hist)

            with _fund_lock:
                fund_info = _cache["fundamentals"].get(ticker, {})
            fund_score, fund_det = calculate_fundamental_score(fund_info)

            combined = round((tech_score + fund_score) / 2)
            signal   = generate_signal(tech_det, fund_det)
            reason   = generate_reason(tech_det, fund_det, combined)

            name = (fund_det.get("long_name") or
                    fund_info.get("shortName", "") or
                    ticker.replace(".NS", "").replace(".BO", ""))

            currency = fund_det.get("currency") or ("INR" if ".NS" in ticker or ".BO" in ticker else "USD")

            sparkline = [round(safe_float(v), 2) for v in closes.tail(14).tolist()]

            vol = int(safe_float(hist["Volume"].iloc[-1])) if "Volume" in hist.columns else 0

            results.append({
                "ticker":           ticker,
                "name":             name,
                "region":           meta[ticker]["region"],
                "industry":         meta[ticker]["industry"],
                "currency":         currency,
                "current_price":    round(price, 2),
                "prev_price":       round(prev,  2),
                "change_pct":       round(chg_pct, 2),
                "change_abs":       round(price - prev, 2),
                "volume":           vol,
                "market_cap":       fund_det.get("market_cap"),
                "technical_score":  tech_score,
                "fundamental_score": fund_score,
                "combined_score":   combined,
                "signal":           signal,
                "reason":           reason,
                "tech_details":     tech_det,
                "fund_details":     fund_det,
                "sparkline":        sparkline,
                "high_90d":         round(safe_float(closes.max()), 2),
                "low_90d":          round(safe_float(closes.min()), 2),
                "last_updated":     datetime.now().isoformat(),
            })
        except Exception as e:
            pass  # Skip silently; don't crash the whole batch

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scored {len(results)}/{n} stocks successfully.")
    return results


def refresh_stocks():
    with _cache_lock:
        _cache["loading"] = True
    try:
        stocks = fetch_all_stocks()
        if stocks:
            with _cache_lock:
                _cache["stocks"]       = stocks
                _cache["last_updated"] = datetime.now().isoformat()
    finally:
        with _cache_lock:
            _cache["loading"] = False


def _bg_fundamentals():
    """Background thread: refresh fundamentals for all tickers, then re-score."""
    tickers = [t for r in STOCK_UNIVERSE.values() for ind in r.values() for t in ind]
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Background fundamentals fetch for {len(tickers)} tickers …")
    for ticker in tickers:
        try:
            fetch_fundamentals_one(ticker)
            time.sleep(0.35)  # ~3 req/s — polite to Yahoo
        except Exception:
            pass
    save_fundamentals_to_disk()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fundamentals done. Re-scoring …")
    refresh_stocks()


def _periodic_price_refresh():
    """Background thread: refresh prices every PRICE_TTL seconds."""
    while True:
        time.sleep(PRICE_TTL)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Scheduled price refresh …")
        refresh_stocks()


# ─── AI Analysis ───────────────────────────────────────────────────────────────

def _build_picks_payload(stocks):
    picks = []
    for s in sorted(stocks, key=lambda x: x["combined_score"], reverse=True)[:10]:
        picks.append({
            "ticker":             s["ticker"],
            "name":               s["name"],
            "industry":           s["industry"],
            "region":             s["region"],
            "change_pct":         s["change_pct"],
            "combined_score":     s["combined_score"],
            "technical_score":    s["technical_score"],
            "fundamental_score":  s["fundamental_score"],
            "rsi":                s["tech_details"].get("rsi"),
            "five_day_return":    s["tech_details"].get("five_day_return"),
            "pe_ratio":           s["fund_details"].get("pe_ratio"),
            "revenue_growth_pct": s["fund_details"].get("revenue_growth_pct"),
            "profit_margin_pct":  s["fund_details"].get("profit_margin_pct"),
            "signal":             s["signal"],
        })
    return picks


def _build_prompt(picks):
    return f"""You are a senior equity research analyst. Our quantitative screening model flagged these stocks today based on combined technical + fundamental signals:

{json.dumps(picks, indent=2)}

For each stock give:
1. thesis — one crisp sentence on WHY it scored well today
2. key_risk — the single biggest risk to this thesis
3. conviction — HIGH, MEDIUM, or LOW (be honest; not everything is HIGH)

Then write 2-3 sentences of macro_themes identifying patterns across these picks (sectors, geographies, market regimes).

Respond ONLY with valid JSON — no markdown, no extra text:
{{
  "picks": [
    {{"ticker": "...", "thesis": "...", "key_risk": "...", "conviction": "HIGH|MEDIUM|LOW"}}
  ],
  "macro_themes": "..."
}}"""


def _parse_json_response(text):
    text = text.strip()
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError("No JSON found in response")


def _analyze_with_groq(prompt):
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("No GROQ_API_KEY set")

    body = json.dumps({
        "model":       "llama-3.3-70b-versatile",
        "max_tokens":  1800,
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=body,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
        }
    )
    with urllib.request.urlopen(req, timeout=35) as resp:
        result = json.loads(resp.read().decode())
        return result["choices"][0]["message"]["content"]


def _analyze_with_gemini(prompt):
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("No GEMINI_API_KEY set")

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1800},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
        data=body,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=35) as resp:
        result = json.loads(resp.read().decode())
        return result["candidates"][0]["content"]["parts"][0]["text"]


GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
]

def _groq_request(messages, system=None, max_tokens=1800):
    """Try each Groq model until one works."""
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("No GROQ_API_KEY")
    msgs = ([{"role":"system","content":system}] if system else []) + messages
    last_err = None
    for i, model in enumerate(GROQ_MODELS):
        try:
            body = json.dumps({
                "model": model, "max_tokens": max_tokens,
                "messages": msgs, "temperature": 0.3,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions", data=body,
                headers={"Content-Type":"application/json","Authorization":f"Bearer {api_key}"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())["choices"][0]["message"]["content"]
        except Exception as e:
            err_str = str(e)
            last_err = e
            if "429" in err_str or "rate" in err_str.lower() or "decommissioned" in err_str or "not supported" in err_str:
                if i < len(GROQ_MODELS) - 1:
                    time.sleep(1.5)  # brief pause before next model
                continue
            raise
    raise Exception(f"rate_limit: {last_err}")


def _gemini_request(messages, system=None, max_tokens=1800):
    """Call Gemini generateContent."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("No GEMINI_API_KEY")
    parts = []
    if system:
        parts.append({"text": system + "\n\n"})
    for m in messages:
        parts.append({"text": f"[{m['role'].upper()}]: {m['content']}\n"})
    body = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
        data=body, headers={"Content-Type":"application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["candidates"][0]["content"]["parts"][0]["text"]


def ai_request(messages, system=None, max_tokens=1800):
    """Try Groq first, fall back to Gemini."""
    groq_key   = os.environ.get("GROQ_API_KEY","")
    gemini_key = os.environ.get("GEMINI_API_KEY","")
    if not groq_key and not gemini_key:
        raise ValueError("Set GROQ_API_KEY or GEMINI_API_KEY")
    groq_rate_limited = False
    if groq_key:
        try:
            return _groq_request(messages, system, max_tokens)
        except Exception as e:
            err_str = str(e)
            if "rate_limit" in err_str or "429" in err_str:
                groq_rate_limited = True
            print(f"Groq failed: {e} — trying Gemini")
    if gemini_key:
        try:
            return _gemini_request(messages, system, max_tokens)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate" in err_str.lower():
                raise Exception("rate_limit")
            raise
    if groq_rate_limited:
        raise Exception("rate_limit")
    raise ValueError("All AI providers failed")


def chat_with_ai(message, history, context):
    """Conversational AI assistant with stock market context."""
    system = """You are Mergeon AI, a professional financial research assistant built into the Mergeon Financial Intelligence platform. You help users understand market signals, stock data, and financial concepts.

Be concise, professional, and data-driven. Use actual numbers from the context when available. Do not give explicit buy/sell advice — frame insights as observations. Do not reveal which AI model or provider powers you."""

    ctx_str = ""
    if context:
        top5 = context.get("top5", [])
        ctx_str = f"\n\nCurrent market snapshot: {context.get('total',0)} stocks tracked, {context.get('gainers',0)} gainers, {context.get('losers',0)} losers today."
        if top5:
            top5_str = ", ".join([f"{s['ticker']}({s['score']}, {s['change']:+.1f}%)" for s in top5])
            ctx_str += f" Top 5 by score: {top5_str}."

    messages = list(history or [])
    messages.append({"role": "user", "content": message + ctx_str})
    return ai_request(messages, system=system, max_tokens=600)


def analyze_top_picks(stocks):
    """Try Groq first, fall back to Gemini."""
    if not stocks:
        return {"error": "No stock data loaded yet."}

    picks  = _build_picks_payload(stocks)
    prompt = _build_prompt(picks)

    # 1. Try Groq
    groq_key   = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    if not groq_key and not gemini_key:
        return {"error": "Set GROQ_API_KEY or GEMINI_API_KEY to enable AI analysis."}

    try:
        text = ai_request([{"role":"user","content":prompt}])
        return _parse_json_response(text)
    except Exception as e:
        err_str = str(e)
        if "rate_limit" in err_str or "429" in err_str:
            return {"error": "AI rate limit reached. Please wait 30 seconds and try again."}
        return {"error": str(e)}


# ─── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, status=200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _html(self, path):
        try:
            with open(path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type",   "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, "Dashboard file not found")

    def do_GET(self):
        p = urlparse(self.path).path

        if p in ("/", "/index.html"):
            self._html(os.path.join(os.path.dirname(__file__), "stock_dashboard.html"))

        elif p == "/api/stocks":
            with _cache_lock:
                stocks  = _cache["stocks"]
                updated = _cache["last_updated"]
                loading = _cache["loading"]
            self._json({
                "stocks":       stocks or [],
                "last_updated": updated,
                "loading":      loading,
                "count":        len(stocks) if stocks else 0,
            })

        elif p == "/api/top-picks":
            with _cache_lock:
                stocks = _cache["stocks"] or []
            top = sorted(stocks, key=lambda x: x["combined_score"], reverse=True)[:20]
            self._json({"picks": top})

        elif p == "/api/refresh":
            threading.Thread(target=refresh_stocks, daemon=True).start()
            self._json({"status": "refresh_started"})

        elif p == "/api/status":
            total = sum(len(t) for r in STOCK_UNIVERSE.values() for t in r.values())
            with _cache_lock:
                loaded  = len(_cache["stocks"]) if _cache["stocks"] else 0
                updated = _cache["last_updated"]
                loading = _cache["loading"]
            self._json({
                "status":        "ok",
                "stocks_loaded": loaded,
                "total_tickers": total,
                "last_updated":  updated,
                "loading":       loading,
                "yfinance":      YFINANCE_OK,
                "pandas":        PANDAS_OK,
            })

        elif p == "/api/news":
            qs  = parse_qs(urlparse(self.path).query)
            cat = qs.get("category", ["markets"])[0]
            if cat not in NEWS_FEEDS:
                cat = "markets"
            arts = get_news(cat)
            self._json({"articles": arts, "category": cat,
                        "categories": list(NEWS_FEEDS.keys())})

        else:
            self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path).path

        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        if p == "/api/analyze":
            with _cache_lock:
                stocks = _cache["stocks"] or []
            if not stocks:
                self._json({"error": "Stock data not loaded yet — try again in a moment."})
            else:
                self._json(analyze_top_picks(stocks))

        elif p == "/api/chat":
            message = body.get("message", "").strip()
            history = body.get("history", [])
            context = body.get("context")
            if not message:
                self._json({"error": "No message provided."})
                return
            try:
                reply = chat_with_ai(message, history, context)
                self._json({"reply": reply})
            except Exception as e:
                err_str = str(e)
                if "rate_limit" in err_str or "429" in err_str:
                    self._json({"reply": "I'm at capacity right now — too many requests to the AI provider. Please wait 30 seconds and try again."})
                else:
                    self._json({"reply": f"I'm unable to respond right now. Please try again in a moment."})

        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        # Suppress routine request logs; only show errors
        if args and len(args) >= 2 and str(args[1])[:1] in ("4", "5"):
            super().log_message(fmt, *args)


# ─── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not YFINANCE_OK or not PANDAS_OK:
        print("Missing dependencies. Run:  pip install yfinance pandas numpy")
        raise SystemExit(1)

    PORT = int(os.environ.get("PORT", 8080))

    print("╔══════════════════════════════════════════════╗")
    print("║   Mergeon Financial Intelligence             ║")
    print(f"║   Stock Analysis Server  —  port {PORT}        ║")
    print("╚══════════════════════════════════════════════╝")

    total = sum(len(t) for r in STOCK_UNIVERSE.values() for t in r.values())
    print(f"Tracking {total} stocks across US & Indian markets.\n")

    # 1. Load disk-cached fundamentals (instant)
    load_fundamentals_from_disk()

    # 2. First price fetch (background, ~30s)
    threading.Thread(target=refresh_stocks, daemon=True).start()

    # 3. Fundamentals refresh (background, ~60s) — starts after prices
    threading.Timer(8.0, lambda: threading.Thread(target=_bg_fundamentals, daemon=True).start()).start()

    # 4. Periodic price refresh every 15 min
    threading.Thread(target=_periodic_price_refresh, daemon=True).start()

    # 5. News feeds (background, starts after 15s)
    threading.Timer(15.0, lambda: threading.Thread(target=fetch_all_news, daemon=True).start()).start()
    threading.Thread(target=_periodic_news_refresh, daemon=True).start()

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"→ http://localhost:{PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSaving fundamentals cache …")
        save_fundamentals_to_disk()
        print("Bye.")
