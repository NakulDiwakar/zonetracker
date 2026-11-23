"""Refreshes live.json with the latest price of every stock listed in zones.json.
Source 1: NSE's free MCP server (cm_get_equity_stocks, about 1 minute behind).
Backup:   Yahoo Finance, only for stocks NSE did not return (or for everything if NSE fails)."""
import asyncio, json, datetime as dt

try:
    from mcp import ClientSession
    try:
        from mcp.client.streamable_http import streamablehttp_client as connect
    except ImportError:  # newer library versions renamed it
        from mcp.client.streamable_http import streamable_http_client as connect
except Exception:
    ClientSession = None
    connect = None

URL = "https://mcp.nseindia.in/cmmkt/mcp"
PREFIXES = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
LIMIT = 500          # the most stocks NSE returns per request
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def find_stocks(obj, out):
    """Collect every record that has a symbol and a last traded price, wherever it sits in the reply."""
    if isinstance(obj, dict):
        if "symbol" in obj and "lastTradedPrice" in obj:
            out.append(obj)
        for v in obj.values():
            find_stocks(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_stocks(v, out)


async def nse_prices(wanted):
    prices, stamp = {}, ""
    async with connect(URL) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()

            async def grab(prefix):
                res = await session.call_tool("cm_get_equity_stocks", {"limit": LIMIT, "symbolFilter": prefix})
                rows = []
                for item in res.content:
                    text = getattr(item, "text", None)
                    if text:
                        try:
                            find_stocks(json.loads(text), rows)
                        except ValueError:
                            pass
                return rows

            queue, calls = list(PREFIXES), 0
            while queue and calls < 400:
                prefix = queue.pop(0)
                calls += 1
                rows = await grab(prefix)
                for r in rows:
                    sym = r.get("symbol")
                    if sym in wanted and r.get("lastTradedPrice"):
                        prices[sym] = round(float(r["lastTradedPrice"]), 2)
                        stamp = max(stamp, str(r.get("latestTimestamp", "")))
                if len(rows) >= LIMIT and len(prefix) < 3:   # too many for one reply: split into longer prefixes
                    queue.extend(prefix + c for c in PREFIXES)
            print("NSE calls made:", calls)
    return prices, stamp


def yahoo_prices(symbols):
    import yfinance as yf
    out = {}
    for k in range(0, len(symbols), 200):
        batch = symbols[k:k + 200]
        d = yf.download([x + ".NS" for x in batch], period="2d", interval="5m", group_by="ticker",
                        auto_adjust=False, threads=True, progress=False)
        for s in batch:
            try:
                c = d[s + ".NS"]["Close"].dropna()
                if len(c):
                    out[s] = round(float(c.iloc[-1]), 2)
            except Exception:
                pass
    return out


def main():
    syms = [s["s"] for s in json.load(open("zones.json"))["stocks"]]
    prices, stamp, nse_n = {}, "", 0
    if connect is not None and ClientSession is not None:
        try:
            prices, stamp = asyncio.run(nse_prices(set(syms)))
            nse_n = len(prices)
        except Exception as e:
            print("NSE failed, using Yahoo for everything:", repr(e)[:300])
    else:
        print("mcp library missing, using Yahoo for everything")
    missing = [s for s in syms if s not in prices]
    y_n = 0
    if missing:
        try:
            got = yahoo_prices(missing)
            prices.update(got)
            y_n = len(got)
        except Exception as e:
            print("Yahoo failed:", repr(e)[:300])
    src = "NSE" if nse_n and not y_n else ("NSE + Yahoo" if nse_n else "Yahoo Finance")
    with open("live.json", "w") as f:
        json.dump({"t": dt.datetime.now(IST).isoformat(timespec="minutes"), "src": src, "p": prices},
                  f, separators=(",", ":"))
    print(f"{len(prices)} prices written: {nse_n} from NSE, {y_n} from Yahoo. Latest NSE price time: {stamp}")


if __name__ == "__main__":
    main()
