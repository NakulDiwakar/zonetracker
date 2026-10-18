"""Sends a daily Telegram message from zones.json: market mood, then stocks that are
inside a zone now, have a new zone today, or got closer to a zone.

Needs two GitHub Secrets: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
If they are missing, the message is only printed (a safe dry run), nothing is sent.
Uses only built-in Python, nothing to install.
"""
import os, json, sys, urllib.request, urllib.parse, urllib.error

# ---------------- your switches (True = on, False = off) ----------------
SEND_ALERT = True
SHOW_MOOD = True                 # market mood block at the top
SHOW_STRENGTH = True             # strength tag next to each stock (and strong sectors line)
SHOW_INSIDE = True               # section: price is inside the zone now
SHOW_NEW = True                  # section: zone is new since the previous trading day
SHOW_CLOSER = True               # section: price got closer to the zone
ONLY_STRONG_FOR_DEMAND = False   # True = hide demand-zone stocks that are weaker than Nifty (3 months)

INDEX_FILTER = "N500"            # N50, BANK, NN50, N100, MID150, SML250, MICRO250, N500, or "" for all stocks
MIN_VALUE_CR = 10                # skip stocks trading less than this many crore rupees a day
TIMEFRAMES = ["D", "W", "M", "Q", "H"]   # D day, W week, M month, Q quarter, H half-year
SIDES = ["D", "S"]               # D demand (buy zones), S supply (sell zones)
ZONE_TYPES = ["S", "M", "G", "C"]        # S single candle, M multi-candle legout, G gap, C multi-base continuation
MAX_PER_SECTION = 10             # keeps the message short
# ------------------------------------------------------------------------

TF_NAME = {"D": "Daily", "W": "Weekly", "M": "Monthly", "Q": "Quarterly", "H": "Half-yearly"}
TF_ORDER = {"H": 0, "Q": 1, "M": 2, "W": 3, "D": 4}   # bigger timeframes first


def distance(p, side, top, bot, st):
    if st == "I":
        return 0.0
    return (p - top) / p * 100 if side == "D" else (bot - p) / p * 100


def strength_tag(s):
    if not SHOW_STRENGTH or s.get("r") is None:
        return ""
    star = "💪 " if (s["r"] > 0 and s.get("sg")) else ""
    return f" · {star}RS {s['r']:+.0f}%"


def build_rows(stocks):
    rows = []
    for s in stocks:
        if INDEX_FILTER and INDEX_FILTER not in s.get("i", []):
            continue
        if s.get("v", 0) < MIN_VALUE_CR:
            continue
        for z in s["z"]:
            tf, side, top, bot, st, flag, ty = z[0], z[1], z[2], z[3], z[4], z[5], z[6]
            if tf not in TIMEFRAMES or side not in SIDES or ty not in ZONE_TYPES:
                continue
            if ONLY_STRONG_FOR_DEMAND and side == "D" and (s.get("r") is None or s["r"] < 0):
                continue
            rows.append((s, tf, side, top, bot, st, flag, ty, distance(s["p"], side, top, bot, st)))
    return rows


def line(row):
    s, tf, side, top, bot, st, flag, ty, dist = row
    icon = "🟢" if side == "D" else "🔴"
    kind = "Demand" if side == "D" else "Supply"
    where = "inside now" if st == "I" else f"{dist:.1f}% away"
    return f"{icon} {s['s']} · {kind} {TF_NAME[tf]} · {bot:g}-{top:g} · {where}{strength_tag(s)}"


def section(title, rows):
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: (TF_ORDER[r[1]], r[8]))
    out = [f"{title} ({len(rows)})"]
    out += [line(r) for r in rows[:MAX_PER_SECTION]]
    if len(rows) > MAX_PER_SECTION:
        out.append(f"... and {len(rows) - MAX_PER_SECTION} more on the page")
    out.append("")
    return out


def mood_lines(data):
    m = data.get("mood") or {}
    if not m:
        return []
    out = [f"🌡️ Market mood: {m.get('label', 'Unknown')} ({m.get('score', 0)}/{m.get('of', 0)} checks)"]
    if "nifty" in m:
        txt = f"Nifty {m['nifty']:,.0f} · 50-day avg {m['n50']:,.0f}"
        if "n200" in m:
            txt += f" · 200-day avg {m['n200']:,.0f}"
        out.append(txt)
    if "breadth" in m:
        out.append(f"Stocks above their 50-day avg: {m['breadth']}%")
    if "vix" in m:
        out.append(f"India VIX: {m['vix']}")
    if SHOW_STRENGTH and data.get("top_sectors"):
        out.append("Strongest sectors (3 months): " + ", ".join(g for g, _ in data["top_sectors"][:3]))
    out.append("")
    return out


def build_message(data):
    lines = [f"📊 Zone alert · prices as of {data.get('asof', '?')}", ""]
    if SHOW_MOOD:
        lines += mood_lines(data)
    rows = build_rows(data["stocks"])
    inside = [r for r in rows if r[5] == "I"]
    new = [r for r in rows if r[5] != "I" and r[6] == "N"]
    closer = [r for r in rows if r[5] != "I" and r[6] == "C"]
    if SHOW_INSIDE:
        lines += section("📍 Inside a zone now", inside)
    if SHOW_NEW:
        lines += section("🆕 New zones today", new)
    if SHOW_CLOSER:
        lines += section("➡️ Got closer", closer)
    if len(lines) <= 3 and not (inside or new or closer):
        lines.append("Nothing new matched your filters today.")
    if SHOW_STRENGTH:
        lines.append("RS = stock's 3-month return minus Nifty's. 💪 = beating Nifty and in a strong sector.")
    lines.append("This is a scanner alert, not advice. Check the chart before any trade.")
    return "\n".join(lines)


def send(token, chat_id, text):
    # Telegram allows about 4096 characters per message, so long messages are split at line breaks
    parts, cur = [], ""
    for ln in text.split("\n"):
        if len(cur) + len(ln) + 1 > 3800:
            parts.append(cur)
            cur = ""
        cur += ln + "\n"
    parts.append(cur)
    for p in parts:
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": p}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except urllib.error.HTTPError as e:
            sys.exit(f"Telegram refused the message: {e.code} {e.read().decode()[:200]}")
        except Exception as e:
            sys.exit(f"Could not reach Telegram: {type(e).__name__}")


def main():
    if not SEND_ALERT:
        print("SEND_ALERT is off, nothing sent.")
        return
    with open("zones.json") as f:
        data = json.load(f)
    text = build_message(data)
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram secrets not found, dry run only. The message would be:\n")
        print(text)
        return
    send(token, chat_id, text)
    print("Telegram alert sent.")


if __name__ == "__main__":
    main()
