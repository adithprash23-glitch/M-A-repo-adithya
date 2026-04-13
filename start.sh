#!/bin/bash
python3 -m venv /opt/render/venv
/opt/render/venv/bin/pip install yfinance pandas numpy --quiet
exec /opt/render/venv/bin/python stock_server.py
