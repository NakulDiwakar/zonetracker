"""Builds zones.json for the Zone Proximity Tracker page (version 2).

Setup:  pip install yfinance pandas numpy
Run:    python build_zones.py          (about 750 stocks, tagged by index)

Zone rules are the same as your TradingView indicator: single base + explosive candle, multi-candle
legout, continuation zones (rally-base-rally / drop-base-drop), gap zones, and long-wick base candles.
Timeframes: D, W, M, Q = calendar quarter, H = calendar half-year.

For each stock and timeframe the file lists:
  fresh zone  (st "F") - the nearest zone price has not touched yet
  inside zone (st "I") - a zone the latest price is sitting in right now. A zone that was only touched by a
                         wick and then left behind (price bounced away) is NOT shown: it has done its work.
Each zone also gets a flag: "N" new since the previous trading day, "C" got closer, or "".
Each stock gets "v", its average daily traded value in rupee crore over the last 20 days.
"""
import sys, os, json, time, datetime as dt
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

# ---- zone rules (same as the indicator) ----
ATR_LEN, IMP_MULT, BASE_MULT = 14, 1.5, 0.6
USE_WICK, WICK_PCT, WICK_RNG = True, 0.35, 1.5
USE_MULTI, MULTI_MAX, MULTI_MULT, MULTI_MIN_BODY = True, 3, 1.5, 0.3
USE_CONT, CONT_MAX, CONT_NEED_LEG, LEG_BARS, LEG_MULT, CONT_HEIGHT = True, 3, True, 3, 1.5, 2.5
USE_GAPS, GAP_MULT, GAP_NEED_BASE = True, 0.3, False
MAX_ZONES = 60
KEEP = {"D": 1500, "W": 600, "M": 400, "Q": 200, "H": 100}   # newest candles used per timeframe
CLOSER_PCT = 0.2          # "got closer" = at least this many % points nearer than the previous day
COLS = ["Open", "High", "Low", "Close"]


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


def engine(o, h, l, c):
    """Runs the indicator's zone rules candle by candle.
    Returns fresh demand zones, fresh supply zones, demand zones being tested by the latest candle,
    supply zones being tested by the latest candle, and the last close. Zones are (top, bottom)."""
    n = len(c)
    first = max(MULTI_MAX, CONT_MAX + LEG_BARS) + 3
    if n < first + 2:
        return [], [], [], [], c[-1]
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    atr = pd.Series(tr).ewm(alpha=1 / ATR_LEN, adjust=False).mean().values
    body, rng = abs(c - o), h - l
    isb = (body < BASE_MULT * atr) | (USE_WICK & (rng > 0) & (body <= WICK_PCT * rng) & (rng <= WICK_RNG * atr))

    dz, sz, lastD, lastS = [], [], -1, -1
    din, sin_ = [], []
    for i in range(first, n):
        if i == n - 1:      # zones fresh until now where the latest price is sitting inside the zone range
            din = [z for z in dz if z[1] <= c[i] <= z[0]]
            sin_ = [z for z in sz if z[1] <= c[i] <= z[0]]
        dz = [z for z in dz if not (c[i] < z[1] or l[i] <= z[0])]
        sz = [z for z in sz if not (c[i] > z[0] or h[i] >= z[1])]

        a1, b1 = atr[i - 1], isb[i - 1]
        impUp = c[i] > o[i] and c[i] - o[i] > IMP_MULT * a1
        impDn = c[i] < o[i] and o[i] - c[i] > IMP_MULT * a1
        bullImp, bearImp = b1 and impUp, b1 and impDn

        bullLen = bearLen = 0
        if USE_MULTI:
            for L in range(2, MULTI_MAX + 1):
                if not isb[i - L]:
                    continue
                aL = atr[i - L]
                bull = all(c[i - j] > o[i - j] and body[i - j] >= MULTI_MIN_BODY * aL for j in range(L))
                bear = all(c[i - j] < o[i - j] and body[i - j] >= MULTI_MIN_BODY * aL for j in range(L))
                if bullLen == 0 and bull and c[i] - o[i - L + 1] >= MULTI_MULT * aL:
                    bullLen = L
                if bearLen == 0 and bear and o[i - L + 1] - c[i] >= MULTI_MULT * aL:
                    bearLen = L

        cBull = cBear = 0
        cd = cs = None
        if USE_CONT and b1 and (impUp or impDn):
            allb = True
            bTop, bBot = max(o[i - 1], c[i - 1]), min(o[i - 1], c[i - 1])
            hi, lo = h[i - 1], l[i - 1]
            for N in range(2, CONT_MAX + 1):
                allb = allb and isb[i - N]
                bTop, bBot = max(bTop, o[i - N], c[i - N]), min(bBot, o[i - N], c[i - N])
                hi, lo = max(hi, h[i - N]), min(lo, l[i - N])
                hOk = (hi - lo) <= CONT_HEIGHT * a1
                lBull = (c[i - N - 1] - o[i - N - LEG_BARS]) >= LEG_MULT * atr[i - N - 1]
                lBear = (o[i - N - LEG_BARS] - c[i - N - 1]) >= LEG_MULT * atr[i - N - 1]
                if allb and hOk and impUp and c[i] > hi and (not CONT_NEED_LEG or lBull):
                    cBull, cd = N, (bTop, lo)
                if allb and hOk and impDn and c[i] < lo and (not CONT_NEED_LEG or lBear):
                    cBear, cs = N, (hi, bBot)

        okG = (not GAP_NEED_BASE) or b1
        gapUp = USE_GAPS and okG and not bullImp and l[i] > h[i - 1] and (l[i] - h[i - 1]) > GAP_MULT * a1
        gapDn = USE_GAPS and okG and not bearImp and h[i] < l[i - 1] and (l[i - 1] - h[i]) > GAP_MULT * a1
        dMulti = bullLen > 0 and not bullImp and (i - bullLen) != lastD
        sMulti = bearLen > 0 and not bearImp and (i - bearLen) != lastS

        if cBull:
            dz.append(cd); lastD = i - 1
        elif bullImp:
            dz.append((max(o[i - 1], c[i - 1]), l[i - 1])); lastD = i - 1
        elif dMulti:
            j = i - bullLen; dz.append((max(o[j], c[j]), l[j])); lastD = j
        elif gapUp:
            dz.append((max(o[i - 1], c[i - 1]), l[i - 1])); lastD = i - 1
        if cBear:
            sz.append(cs); lastS = i - 1
        elif bearImp:
            sz.append((h[i - 1], min(o[i - 1], c[i - 1]))); lastS = i - 1
        elif sMulti:
            j = i - bearLen; sz.append((h[j], min(o[j], c[j]))); lastS = j
        elif gapDn:
            sz.append((h[i - 1], min(o[i - 1], c[i - 1]))); lastS = i - 1
        dz, sz = dz[-MAX_ZONES:], sz[-MAX_ZONES:]
    return dz, sz, din, sin_, c[-1]


def pick(dz, sz, din, sin_, c0):
    """The nearest fresh zone on each side, plus the nearest zone being tested now on each side."""
    out = []
    fd = [z for z in dz if z[0] < c0]
    fs = [z for z in sz if z[1] > c0]
    if fd:
        out.append(("D", max(fd), "F"))
    if fs:
        out.append(("S", min(fs, key=lambda q: q[1]), "F"))
    if din:
        out.append(("D", max(din), "I"))
    if sin_:
        out.append(("S", min(sin_, key=lambda q: q[1]), "I"))
    return out


def dist(p, side, top, bot, st):
    if st == "I":
        return 0.0
    return (p - top) / p * 100 if side == "D" else (bot - p) / p * 100


def snapshot(old):
    """Distances of every zone in an older zones.json, used as yesterday's baseline."""
    base = {}
    for s in old.get("stocks", []):
        m = {}
        for z in s["z"]:
            st = z[4] if len(z) > 4 else "F"
            m[f"{z[0]}|{z[1]}|{z[2]}|{z[3]}"] = round(dist(s["p"], z[1], z[2], z[3], st), 2)
        base[s["s"]] = m
    return base


def clean(df):
    df = df.dropna()
    df.index = pd.DatetimeIndex(df.index).tz_localize(None)
    return df


def main():
    hdr = {"User-Agent": "Mozilla/5.0"}
    codes = {}
    for code, (name, fn) in INDICES.items():
        try:
            lst = pd.read_csv(BASE_URL + fn, storage_options=hdr)
            for sym in lst["Symbol"].astype(str).str.strip():
                codes.setdefault(sym, []).append(code)
        except Exception as e:
            print("Skipped", name, "-", e)
    if not codes:
        sys.exit("Could not download any index list")
    syms = sorted(codes)

    out = []
    kw = dict(interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False)
    for k in range(0, len(syms), 100):
        batch = syms[k:k + 100]
        tk = [x + ".NS" for x in batch]
        full = yf.download(tk, period="max", **kw)
        recent = yf.download(tk, period="7d", **kw)
        for s_, t in zip(batch, tk):
            try:
                d = clean(full[t][COLS + ["Volume"]])
            except KeyError:
                continue
            if len(d) < 30:
                continue
            try:
                r = clean(recent[t][COLS + ["Volume"]])
                d = pd.concat([d, r[r.index > d.index[-1]]])
            except Exception:
                pass
            zl = []
            for tf in ["D", "W", "M", "Q", "H"]:
                x = agg(d[COLS], tf).tail(KEEP[tf])
                if len(x) < 20:
                    continue
                dz, sz, din, sin_, c0 = engine(*[x[c].values for c in COLS])
                for side, z, st in pick(dz, sz, din, sin_, c0):
                    zl.append([tf, side, round(float(z[0]), 2), round(float(z[1]), 2), st])
            v = round(float((d["Close"] * d["Volume"]).tail(20).mean() / 1e7), 2)
            out.append({"s": s_, "p": round(float(d["Close"].iloc[-1]), 2), "d": d.index[-1].date().isoformat(),
                        "v": v, "i": codes[s_], "z": zl})
        print(f"Done {min(k + 100, len(syms))} of {len(syms)}")
        time.sleep(2)

    asof = max(x["d"] for x in out)

    # Yesterday's baseline: the previous zones.json, or the baseline already kept if this is a same-day re-run
    old = None
    if os.path.exists("zones.json"):
        try:
            with open("zones.json") as f:
                old = json.load(f)
        except Exception:
            old = None
    if old and old.get("asof") == asof:
        base = old.get("base", {})
    elif old:
        base = snapshot(old)
    else:
        base = {}
    for rec in out:
        prevz = base.get(rec["s"])
        for z in rec["z"]:
            fl = ""
            if prevz is not None:
                key = f"{z[0]}|{z[1]}|{z[2]}|{z[3]}"
                if key not in prevz:
                    fl = "N"
                elif prevz[key] - dist(rec["p"], z[1], z[2], z[3], z[4]) >= CLOSER_PCT:
                    fl = "C"
            z.append(fl)

    with open("zones.json", "w") as f:
        json.dump({"universe": "NSE stocks", "generated": dt.datetime.now().isoformat(timespec="minutes"),
                   "asof": asof, "indices": {c: v[0] for c, v in INDICES.items()}, "base": base,
                   "stocks": out}, f, separators=(",", ":"))
    print(f"Wrote zones.json with {len(out)} stocks, prices as of {asof}")


if __name__ == "__main__":
    main()
