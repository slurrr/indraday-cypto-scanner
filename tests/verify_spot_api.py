import requests
import json

def check_spot_data():
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "limit": 5
    }
    print(f"Fetching Spot Klines from {url}...")
    resp = requests.get(url, params=params)
    data = resp.json()
    
    print(f"Received {len(data)} candles.")
    if len(data) > 0:
        k = data[0]
        print("Sample Candle Fields:")
        print(f"0 (Time): {k[0]}")
        print(f"5 (Vol): {k[5]}")
        print(f"9 (TakerBuyVol): {k[9]}")
        
        vol = float(k[5])
        taker = float(k[9])
        print(f"Parsed Vol: {vol}")
        print(f"Parsed Taker: {taker}")
        
        if vol > 0:
            print(f"Taker/Vol Ratio: {taker/vol:.4f}")
        else:
            print("Volume is 0?")

if __name__ == "__main__":
    check_spot_data()
