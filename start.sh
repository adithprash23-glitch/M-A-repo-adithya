#!/bin/bash
pip3 install yfinance pandas numpy --target=./packages --quiet 2>&1
PYTHONPATH=./packages exec python3 stock_server.py
