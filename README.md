# Intraday Flow Scanner - MVP

A meaningful real-time decision support system for intraday crypto trading.

## Purpose
To tell the trader **where to look and when**, using price structure, VWAP, volatility, and flow intelligence.

## Setup

1. Set up Virtual Environment:
   ```bash
   # Windows
   python -m venv venv
   .\venv\Scripts\activate
   
   # Linux/Mac
   python3 -m venv venv
   source venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the scanner:
   ```bash
   python main.py
   ```

## Structure
- `config/`: Settings and constants.
- `core/`: Business logic (aggregations, calculating indicators, pattern detection).
- `data/`: Binance websocket connection.
- `models/`: Type definitions and patterns.
- `ui/`: Console output.
