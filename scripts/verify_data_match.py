
import requests
import time
import json

SYMBOL = "BTCUSDT"
INTERVAL = "5m"
LIMIT = 2

def get_klines():
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": SYMBOL, "interval": INTERVAL, "limit": LIMIT}
    resp = requests.get(url, params=params).json()
    # Return last closed candle
    k = resp[-2] # -1 is open, -2 is closed
    vol = float(k[5])
    taker_buy = float(k[9])
    cvd = 2 * taker_buy - vol
    print(f"KLINE [Timestamp {k[0]}]: Vol={vol:.4f}, TakerBuy={taker_buy:.4f}, CVD={cvd:.4f}")
    return int(k[0]), int(k[6]), cvd

def get_agg_trades(start, end):
    url = "https://api.binance.com/api/v3/aggTrades"
    all_trades = []
    curr = start
    while True:
        params = {"symbol": SYMBOL, "startTime": curr, "endTime": end, "limit": 1000}
        resp = requests.get(url, params=params).json()
        if not resp: break
        all_trades.extend(resp)
        last_ts = int(resp[-1]['T'])
        if last_ts >= end: break
        curr = last_ts + 1
        if len(resp) < 1000: break
        
    # Calculate CVD
    vol = 0.0
    buy_vol = 0.0
    for t in all_trades:
        q = float(t['q'])
        ts = int(t['T'])
        if ts > end: continue
        
        vol += q
        if t['m']: # specific to Spot: m=True means Maker is Buyer -> Taker is Seller? 
             # Wait. 
             # is_buyer_maker = True -> Maker is Buyer. Taker is Seller.
             # is_buyer_maker = False -> Maker is Seller. Taker is Buyer.
             # Taker Buy Volume means Taker was Buyer (is_buyer_maker=False).
             pass
        else:
             buy_vol += q
             
    cvd = 2 * buy_vol - vol
    print(f"TRADES [Timestamp {start}]: Vol={vol:.4f}, TakerBuy={buy_vol:.4f}, CVD={cvd:.4f}")
    return cvd

def main():
    print(f"Verifying {SYMBOL} Data Consistency...")
    start, end, kline_cvd = get_klines()
    trade_cvd = get_agg_trades(start, end)
    
    diff = abs(kline_cvd - trade_cvd)
    pct = (diff / abs(trade_cvd)) * 100 if trade_cvd != 0 else 0
    
    print(f"\nDelta: {diff:.4f} ({pct:.2f}%)")
    if pct > 5.0:
        print("FAIL: Significant Discrepancy detected!")
    else:
        print("PASS: Data sources are consistent.")

if __name__ == "__main__":
    main()
