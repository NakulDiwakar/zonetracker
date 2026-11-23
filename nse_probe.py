"""Probe for NSE's free "CM Market Live" MCP server. It only READS data and changes nothing.
It prints the server's tools, how fresh its data is, and a few quotes, so we can see what is possible."""
import asyncio, json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "https://mcp.nseindia.in/cmmkt/mcp"
SYMBOLS = ["RELIANCE", "TCS", "GRANULES"]


def show(result):
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        print((text if text is not None else str(item))[:1500])


async def main():
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS FOUND:", len(tools.tools))
            for t in tools.tools:
                print("-", t.name, "|", (t.description or "")[:120].replace("\n", " "))
                print("    inputs:", json.dumps((t.inputSchema or {}).get("properties", {}))[:300])
            names = {t.name for t in tools.tools}
            if "cm_get_data_status" in names:
                print("\nDATA STATUS")
                show(await session.call_tool("cm_get_data_status", {}))
            if "cm_get_stock_quote" in names:
                for s in SYMBOLS:
                    print("\nQUOTE", s)
                    show(await session.call_tool("cm_get_stock_quote", {"symbol": s}))


asyncio.run(main())
