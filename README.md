# Mergeon

Mergeon is a stock screener and market news dashboard for US and Indian stocks. It pulls price history and company financials from Yahoo Finance, gives every stock a score out of 100, and then has an LLM write a short take on the ones that scored highest. There's also a news page and a chat box where you can ask about what's on the screen.

Live version: https://m-a-repo-adithya.vercel.app/

It started out as an M&A news tracker, which is where the name comes from. That older version is still in the repo as `server.py` but the current app doesn't use it.

## What it does

**Screener.** 84 stocks across 6 US sectors (tech, healthcare, finance, energy, consumer, industrial) and 6 Indian ones (IT, banking, FMCG, pharma, auto, infrastructure). You can filter by region and sector and switch between a table and a grid.

**Scoring.** Every stock gets two scores out of 100 and the combined score is just the average of the two.

- Technical score: RSI (up to 30 points, it gives the most points in the 30 to 40 range since that's usually a stock recovering from being oversold), MACD vs its signal line (25), where the price sits compared to the 20 and 50 day moving averages (25), and the 5 day return (20).
- Fundamental score: P/E (30), revenue growth (25), debt to equity (25) and profit margin (20).

Each stock also gets short tags like "Oversold", "MACD Bullish" or "Cheap Valuation" and a one line reason for its score, so you can see why it ranked where it did instead of just a number.

**AI top picks.** The top 10 by combined score get sent to Groq (Llama 3.3 70B), and if Groq is down or rate limited it falls back to Gemini 2.0 Flash. For each stock it returns a one sentence thesis, the biggest risk, and a conviction level.

**News.** RSS feeds sorted into markets, technology, energy, finance, healthcare, real estate and India. Articles get ranked by how relevant they look, so the actual market news shows up above the filler.

**Chat.** Ask questions about the stocks or general market stuff. It gets the current snapshot (number of gainers and losers, the top 5) as context so it can answer about what's actually on screen.

None of this is financial advice. The scores are simple rules I picked, not a model trained on anything.

## Running it locally

You need Python 3 and pip.

```bash
pip install -r requirements.txt
export GROQ_API_KEY=your_key      # free at console.groq.com
export GEMINI_API_KEY=your_key    # optional, only used as the backup
python3 stock_server.py
```

Then open http://localhost:8080. The first price load takes around 30 seconds since it downloads history for all 84 stocks, and fundamentals fill in about a minute after that. Fundamentals get cached to `fundamentals_cache.json` so restarts are faster. Without an API key everything works except the AI picks and the chat.

## Deployment

- **Vercel** (the live link) runs `api/index.py` as a serverless function. It only tracks 48 stocks because Vercel functions have a time limit and the full list takes too long to download in one request.
- **Render** runs `stock_server.py` through `start.sh`, configured in `render.yaml`.

## Files

| File | What it is |
| --- | --- |
| `stock_server.py` | Main backend: data fetching, scoring, news, AI calls, and the HTTP server |
| `api/index.py` | Vercel version of the same backend with the smaller stock list |
| `stock_dashboard.html` | The whole frontend, one file with no framework |
| `server.py` | Old M&A news server, not used anymore |
| `render.yaml`, `start.sh`, `Procfile` | Render deployment |
| `vercel.json` | Vercel routing |
