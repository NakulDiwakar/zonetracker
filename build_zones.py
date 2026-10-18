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

# ---- market mood and strength settings ----
VIX_CALM = 18        # India VIX below this counts as calm
BREADTH_OK = 50      # at least this % of stocks above their 50-day average counts as healthy
RS_DAYS = 63         # about 3 months of trading days, used for relative strength against Nifty


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
    supply zones being tested by the latest candle, and the last close. Zones are (top, bottom, type): S single candle, M multi-candle legout, C multi-base continuation, G gap."""
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
            dz.append((cd[0], cd[1], "C")); lastD = i - 1
        elif bullImp:
            dz.append((max(o[i - 1], c[i - 1]), l[i - 1], "S")); lastD = i - 1
        elif dMulti:
            j = i - bullLen; dz.append((max(o[j], c[j]), l[j], "M")); lastD = j
        elif gapUp:
            dz.append((max(o[i - 1], c[i - 1]), l[i - 1], "G")); lastD = i - 1
        if cBear:
            sz.append((cs[0], cs[1], "C")); lastS = i - 1
        elif bearImp:
            sz.append((h[i - 1], min(o[i - 1], c[i - 1]), "S")); lastS = i - 1
        elif sMulti:
            j = i - bearLen; sz.append((h[j], min(o[j], c[j]), "M")); lastS = j
        elif gapDn:
            sz.append((h[i - 1], min(o[i - 1], c[i - 1]), "G")); lastS = i - 1
        dz, sz = dz[-MAX_ZONES:], sz[-MAX_ZONES:]
    return dz, sz, din, sin_, c[-1]


def pick(dz, sz, din, sin_, c0):
    """The nearest fresh zone on each side, plus the nearest zone being tested now on each side."""
    out = []
    for ty in "SMCG":          # one nearest zone per zone type, so the page can switch types on and off
        fd = [z for z in dz if z[2] == ty and z[0] < c0]
        fs = [z for z in sz if z[2] == ty and z[1] > c0]
        idn = [z for z in din if z[2] == ty]
        isn = [z for z in sin_ if z[2] == ty]
        if fd:
            out.append(("D", max(fd), "F"))
        if fs:
            out.append(("S", min(fs, key=lambda q: q[1]), "F"))
        if idn:
            out.append(("D", max(idn), "I"))
        if isn:
            out.append(("S", min(isn, key=lambda q: q[1]), "I"))
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


def close_series(ticker):
    """Daily closes for an index (Nifty 50 or India VIX). If the download fails, returns None and the build carries on."""
    try:
        x = yf.download(ticker, period="2y", interval="1d", auto_adjust=False, progress=False)
        c = x["Close"]
        if isinstance(c, pd.DataFrame):
            c = c.iloc[:, 0]
        return c.dropna()
    except Exception as e:
        print("Could not get", ticker, "-", e)
        return None


def ret_over(s, n):
    """Percentage change over the last n candles."""
    if s is None or len(s) <= n:
        return None
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) * 100


def make_mood(nifty, vix, above50, total50):
    """Simple market mood from three checks: Nifty above its 50-day average, breadth, and calm VIX."""
    m, score, checks = {}, 0, 0
    if nifty is not None and len(nifty) >= 50:
        n = float(nifty.iloc[-1])
        m["nifty"] = round(n, 1)
        m["n50"] = round(float(nifty.tail(50).mean()), 1)
        if len(nifty) >= 200:
            m["n200"] = round(float(nifty.tail(200).mean()), 1)
        checks += 1
        score += int(n > m["n50"])
    if total50:
        pct = round(above50 / total50 * 100)
        m["breadth"] = pct
        checks += 1
        score += int(pct >= BREADTH_OK)
    if vix is not None and len(vix):
        v = round(float(vix.iloc[-1]), 1)
        m["vix"] = v
        checks += 1
        score += int(v < VIX_CALM)
    m["score"], m["of"] = score, checks
    if checks == 0:
        m["label"] = "Unknown"
    elif score == checks:
        m["label"] = "Healthy"
    elif score / checks >= 0.5:
        m["label"] = "Mixed"
    else:
        m["label"] = "Weak"
    return m


def main():
    hdr = {"User-Agent": "Mozilla/5.0"}
    codes = {}
    industry = {}
    for code, (name, fn) in INDICES.items():
        try:
            lst = pd.read_csv(BASE_URL + fn, storage_options=hdr)
            has_ind = "Industry" in lst.columns
            for _, row in lst.iterrows():
                sym = str(row["Symbol"]).strip()
                codes.setdefault(sym, []).append(code)
                if has_ind:
                    industry.setdefault(sym, str(row["Industry"]).strip())
        except Exception as e:
            print("Skipped", name, "-", e)
    if not codes:
        sys.exit("Could not download any index list")
    syms = sorted(codes)

    nifty, vix = close_series("^NSEI"), close_series("^INDIAVIX")
    nifty_ret = ret_over(nifty, RS_DAYS)
    above50 = total50 = 0

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
                    zl.append([tf, side, round(float(z[0]), 2), round(float(z[1]), 2), st, "", z[2]])
            v = round(float((d["Close"] * d["Volume"]).tail(20).mean() / 1e7), 2)
            cl = d["Close"]
            if len(cl) >= 50:
                total50 += 1
                above50 += int(cl.iloc[-1] > cl.tail(50).mean())
            sr = ret_over(cl, RS_DAYS)
            rs = None if (sr is None or nifty_ret is None) else round(sr - nifty_ret, 1)
            out.append({"s": s_, "p": round(float(d["Close"].iloc[-1]), 2), "d": d.index[-1].date().isoformat(),
                        "v": v, "i": codes[s_], "r": rs, "g": industry.get(s_, ""), "z": zl})
        print(f"Done {min(k + 100, len(syms))} of {len(syms)}")
        time.sleep(2)

    asof = max(x["d"] for x in out)

    # Strength: each stock's 3-month return minus Nifty's ("r"), and sectors whose median stock is in the top third ("sg")
    by_ind = {}
    for rec in out:
        if rec["r"] is not None and rec["g"]:
            by_ind.setdefault(rec["g"], []).append(rec["r"])
    sect = {g: round(float(np.median(v)), 1) for g, v in by_ind.items() if len(v) >= 3}
    ranked = sorted(sect, key=sect.get, reverse=True)
    top = set(ranked[:max(1, len(ranked) // 3)])
    for rec in out:
        rec["sg"] = 1 if rec["g"] in top else 0
    mood = make_mood(nifty, vix, above50, total50)

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
            z[5] = fl

    with open("zones.json", "w") as f:
        json.dump({"universe": "NSE stocks", "generated": dt.datetime.now().isoformat(timespec="minutes"),
                   "asof": asof, "indices": {c: v[0] for c, v in INDICES.items()}, "base": base,
                   "mood": mood, "top_sectors": [[g, sect[g]] for g in ranked[:5]],
                   "stocks": out}, f, separators=(",", ":"))
    print(f"Wrote zones.json with {len(out)} stocks, prices as of {asof}")


if __name__ == "__main__":
    main()
