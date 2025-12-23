import requests
import json

def check_kline_data(symbol, url):
    params = {
        "symbol": symbol,
        "interval": "15m",
        "limit": 5
    }
    try:
        resp = requests.get(url, params=params)
        data = resp.json()
        if not data:
            print(f"No data for {symbol} from {url}")
            return

        k = data[0]
        print(f"--- {symbol} from {url} ---")
        print(f"Open Time: {k[0]}")
        print(f"Volume (Idx 5): {k[5]}")
        print(f"Taker Buy Base Vol (Idx 9): {k[9]}")
        print(f"Check: Is Idx 9 present? {'Yes' if len(k) > 9 else 'No'}")
        
    except Exception as e:
        print(f"Error: {e}")

print("Checking Spot...")
check_kline_data("BTCUSDT", "https://api.binance.com/api/v3/klines")

print("\nChecking Perp...")
check_kline_data("BTCUSDT", "https://fapi.binance.com/fapi/v1/klines")
