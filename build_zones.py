"""Builds zones.json for the Zone Proximity Tracker page.

Setup:  pip install yfinance pandas numpy
Run:    python build_zones.py 50     (Nifty 50)   or   python build_zones.py 500   (Nifty 500)
Then open the tracker page and click "Load zones.json".

Rule used on every timeframe (D, W, M, Q = calendar quarter, H = calendar half-year):
a base candle (small body, or small body with long wicks) followed by an explosive candle.
Demand zone = top of base body down to base low. Supply zone = base high down to base body bottom.
A zone is kept only while fresh: no later candle touched its entry edge or closed through it.
The latest, still-forming candle counts, so a zone can appear as soon as it turns explosive.
"""
import sys, json, datetime as dt
import numpy as np
import pandas as pd
import yfinance as yf

U = sys.argv[1] if len(sys.argv) > 1 else "50"
URL = f"https://www.niftyindices.com/IndexConstituent/ind_nifty{U}list.csv"
IMP, BASE, WICK_BODY, WICK_RANGE, ATRN = 1.5, 0.6, 0.35, 1.5, 14


def agg(df, tf):
    if tf == "D":
        return df
    if tf == "W":
        g = df.resample("W-FRI")
    else:
        keys = {"M": [df.index.year, df.index.month],
                "Q": [df.index.year, df.index.quarter],
                "H": [df.index.year, (df.index.month > 6).astype(int)]}[tf]
        g = df.groupby(keys)
    return g.agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()


def zones(o, h, l, c):
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    atr = pd.Series(tr).ewm(alpha=1 / ATRN, adjust=False).mean().values
    dz, sz = [], []
    for i in range(1, len(c)):
        b = i - 1
        body, rng = abs(c[b] - o[b]), h[b] - l[b]
        if not (body < BASE * atr[b] or (rng > 0 and body <= WICK_BODY * rng and rng <= WICK_RANGE * atr[b])):
            continue
        if c[i] > o[i] and c[i] - o[i] > IMP * atr[b]:
            dz.append((i, max(o[b], c[b]), l[b]))
        elif c[i] < o[i] and o[i] - c[i] > IMP * atr[b]:
            sz.append((i, h[b], min(o[b], c[b])))
    c0 = c[-1]
    fd = [(t, bt) for i, t, bt in dz
          if t < c0 and not ((l[i + 1:] <= t).any() or (c[i + 1:] < bt).any())]
    fs = [(t, bt) for i, t, bt in sz
          if bt > c0 and not ((h[i + 1:] >= bt).any() or (c[i + 1:] > t).any())]
    return (max(fd) if fd else None), (min(fs, key=lambda z: z[1]) if fs else None)


syms = pd.read_csv(URL, storage_options={"User-Agent": "Mozilla/5.0"})["Symbol"].tolist()
tickers = [s + ".NS" for s in syms]
raw = yf.download(tickers, period="max", interval="1d", group_by="ticker",
                  auto_adjust=False, threads=True, progress=False)

out = []
for s, t in zip(syms, tickers):
    try:
        d = raw[t][["Open", "High", "Low", "Close"]].dropna()
    except KeyError:
        continue
    if len(d) < 30:
        continue
    zl = []
    for tf in ["D", "W", "M", "Q", "H"]:
        x = agg(d, tf)
        if len(x) < 20:
            continue
        dem, sup = zones(*[x[k].values for k in ("Open", "High", "Low", "Close")])
        if dem:
            zl.append([tf, "D", round(float(dem[0]), 2), round(float(dem[1]), 2)])
        if sup:
            zl.append([tf, "S", round(float(sup[0]), 2), round(float(sup[1]), 2)])
    out.append({"s": s, "p": round(float(d.Close.iloc[-1]), 2), "z": zl})

with open("zones.json", "w") as f:
    json.dump({"universe": f"Nifty {U}", "generated": dt.datetime.now().isoformat(timespec="minutes"),
               "stocks": out}, f)
print(f"Wrote zones.json with {len(out)} stocks")
