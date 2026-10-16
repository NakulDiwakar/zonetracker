"""Builds zones.json for the Zone Proximity Tracker page.

Setup:  pip install yfinance pandas numpy
Run:    python build_zones.py        (about 750 stocks: Nifty 500 plus microcap, tagged by index)
Then open the tracker page and click "Load zones.json".

Rule used on every timeframe (D, W, M, Q = calendar quarter, H = calendar half-year):
a base candle (small body, or small body with long wicks) followed by an explosive candle.
Demand zone = top of base body down to base low. Supply zone = base high down to base body bottom.
A zone is kept only while fresh: no later candle touched its entry edge or closed through it.
The latest, still-forming candle counts, so a zone can appear as soon as it turns explosive.
"""
import sys, json, time, datetime as dt
import numpy as np
import pandas as pd
import yfinance as yf

BASE_URL = "https://www.niftyindices.com/IndexConstituent/"
INDICES = {
    "N50": ("Nifty 50", "ind_nifty50list.csv"),
    "BANK": ("Bank Nifty", "ind_niftybanklist.csv"),
    "NN50": ("Nifty Next 50", "ind_niftynext50list.csv"),
    "N100": ("Nifty 100 (large cap)", "ind_nifty100list.csv"),
    "MID150": ("Nifty Midcap 150", "ind_niftymidcap150list.csv"),
    "SML250": ("Nifty Smallcap 250", "ind_niftysmallcap250list.csv"),
    "MICRO250": ("Nifty Microcap 250", "ind_niftymicrocap250_list.csv"),
    "N500": ("Nifty 500", "ind_nifty500list.csv"),
}
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


HDR = {"User-Agent": "Mozilla/5.0"}
COLS = ["Open", "High", "Low", "Close"]
codes = {}
for code, (name, fn) in INDICES.items():
    try:
        lst = pd.read_csv(BASE_URL + fn, storage_options=HDR)
        for sym in lst["Symbol"].astype(str).str.strip():
            codes.setdefault(sym, []).append(code)
    except Exception as e:
        print("Skipped", name, "-", e)
if not codes:
    sys.exit("Could not download any index list")
syms = sorted(codes)


def clean(df):
    df = df.dropna()
    df.index = pd.DatetimeIndex(df.index).tz_localize(None)
    return df


out = []
kw = dict(interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False)
for k in range(0, len(syms), 100):
    batch = syms[k:k + 100]
    tk = [x + ".NS" for x in batch]
    full = yf.download(tk, period="max", **kw)
    recent = yf.download(tk, period="7d", **kw)
    for s_, t in zip(batch, tk):
        try:
            d = clean(full[t][COLS])
        except KeyError:
            continue
        if len(d) < 30:
            continue
        try:
            r = clean(recent[t][COLS])
            d = pd.concat([d, r[r.index > d.index[-1]]])
        except Exception:
            pass
        zl = []
        for tf in ["D", "W", "M", "Q", "H"]:
            x = agg(d, tf)
            if len(x) < 20:
                continue
            dem, sup = zones(*[x[c].values for c in COLS])
            if dem:
                zl.append([tf, "D", round(float(dem[0]), 2), round(float(dem[1]), 2)])
            if sup:
                zl.append([tf, "S", round(float(sup[0]), 2), round(float(sup[1]), 2)])
        out.append({"s": s_, "p": round(float(d.Close.iloc[-1]), 2), "d": d.index[-1].date().isoformat(),
                    "i": codes[s_], "z": zl})
    print(f"Done {min(k + 100, len(syms))} of {len(syms)}")
    time.sleep(2)

asof = max(x["d"] for x in out)
with open("zones.json", "w") as f:
    json.dump({"universe": "NSE stocks", "generated": dt.datetime.now().isoformat(timespec="minutes"),
               "asof": asof, "indices": {c: v[0] for c, v in INDICES.items()}, "stocks": out}, f)
print(f"Wrote zones.json with {len(out)} stocks, prices as of {asof}")
