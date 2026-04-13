#!/bin/bash
python3 -m pip install yfinance pandas numpy --quiet --no-warn-script-location 2>&1 || true
exec python3 stock_server.py
