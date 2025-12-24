
import requests
import time

SYMBOL = "BTCUSDT"

def verify_candle():
    # 1. Get 3 recent 1m Klines
    url = "https://api.binance.com/api/v3/klines"
    # End time now - 2 mins to ensure closed
    now = int(time.time() * 1000)
    end_time = now - 120000
    
    params = {"symbol": SYMBOL, "interval": "1m", "limit": 3, "endTime": end_time}
    klines = requests.get(url, params=params).json()
    
    # Target the middle one
    k = klines[1]
    ts_open = int(k[0])
    ts_close = int(k[6]) # Ensure we use the exact close time from kline
    
    k_vol = float(k[5])
    k_taker = float(k[9])
    k_cvd = 2 * k_taker - k_vol
    
    print(f"KLINE [{ts_open} - {ts_close}]: Vol={k_vol:.6f} Taker={k_taker:.6f} CVD={k_cvd:.6f}")
    
    # 2. Get Agg Trades for EXACT same window
    # Note: aggTrades uses inclusive startTime, inclusive endTime?
    # We must match Binance's candle boundaries. [t, t+59999]
    
    t_url = "https://api.binance.com/api/v3/aggTrades"
    trades = []
    
    # Pagination Loop
    # We iterate from ts_open
    curr = ts_open
    
    # Safety: Don't go past ts_close
    while curr <= ts_close:
        params = {
            "symbol": SYMBOL, 
            "startTime": curr, 
            "endTime": ts_close, 
            "limit": 1000
        }
        resp = requests.get(t_url, params=params).json()
        
        if not resp:
            break
            
        for t in resp:
            t_ts = int(t['T'])
            if t_ts > ts_close:
                continue
            if t_ts < ts_open: # Should not happen given startTime
                continue
                
            trades.append(t)
            
        # Update cursor - take last trade's ID or TS? 
        # AggTrades allows fetching fromId. But by time is easier if we are careful.
        # "If startTime and endTime are both provided, limit should be max 1000."
        # To paginate correctly, we should use fromId if possible, but we don't know the ID.
        # We will use Time. set curr = last_trade_ts + 1
        
        last_t = resp[-1]
        last_ts = int(last_t['T'])
        
        if last_ts >= ts_close:
            break
            
        if last_ts == curr:
             # Stuck on same MS? Advance
             curr += 1
        else:
             curr = last_ts + 1
             
    # Sum it up
    t_vol = 0.0
    t_taker = 0.0
    
    for t in trades:
        q = float(t['q'])
        t_vol += q
        if not t['m']: # Taker BUY
            t_taker += q
            
    t_cvd = 2 * t_taker - t_vol
    
    print(f"TRADES (Count={len(trades)}): Vol={t_vol:.6f} Taker={t_taker:.6f} CVD={t_cvd:.6f}")
    
    diff_vol = abs(k_vol - t_vol)
    diff_cvd = abs(k_cvd - t_cvd)
    
    print(f"\nDiff Vol: {diff_vol:.6f}")
    print(f"Diff CVD: {diff_cvd:.6f}")
    
    if diff_vol < 1.0: # Tolerance
        print("MATCH! (Binance is consistent)")
    else:
        print("MISMATCH! (Data Source issues)")

if __name__ == "__main__":
    verify_candle()
