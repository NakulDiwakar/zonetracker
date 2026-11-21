"""Refreshes live.json with the latest price of every stock listed in zones.json (runs every 5 minutes in market hours)."""
import json, datetime as dt
import yfinance as yf

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
syms = [s["s"] for s in json.load(open("zones.json"))["stocks"]]
prices = {}
for k in range(0, len(syms), 200):
    batch = syms[k:k + 200]
    d = yf.download([x + ".NS" for x in batch], period="2d", interval="5m", group_by="ticker",
                    auto_adjust=False, threads=True, progress=False)
    for s in batch:
        try:
            c = d[s + ".NS"]["Close"].dropna()
            if len(c):
                prices[s] = round(float(c.iloc[-1]), 2)
        except Exception:
            pass
with open("live.json", "w") as f:
    json.dump({"t": dt.datetime.now(IST).isoformat(timespec="minutes"), "p": prices}, f, separators=(",", ":"))
print(len(prices), "prices written")
