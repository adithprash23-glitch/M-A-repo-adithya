#!/bin/bash
python3 -m pip install yfinance pandas numpy --quiet --break-system-packages 2>&1 || true
exec python3 stock_server.py
