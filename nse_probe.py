"""Probe for NSE's free "CM Market Live" MCP server. It only READS data and changes nothing.
It prints the server's tools, how fresh its data is, and a few quotes, so we can see what is possible."""
import asyncio, json
from importlib.metadata import version
from mcp import ClientSession
try:  # the library renamed this function in newer versions, so accept either name
    from mcp.client.streamable_http import streamablehttp_client as connect
except ImportError:
    from mcp.client.streamable_http import streamable_http_client as connect

URL = "https://mcp.nseindia.in/cmmkt/mcp"
SYMBOLS = ["RELIANCE", "TCS", "GRANULES"]


def show(result):
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        print((text if text is not None else str(item))[:1500])


async def main():
    print("mcp library version:", version("mcp"))
    async with connect(URL) as streams:
        read, write = streams[0], streams[1]  # older versions return 3 items, newer ones 2
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS FOUND:", len(tools.tools))
            for t in tools.tools:
                schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
                print("-", t.name, "|", (t.description or "")[:700].replace("\n", " "))
                print("    inputs:", json.dumps(schema.get("properties", {}))[:500])
            names = {t.name for t in tools.tools}
            try:
                if "cm_get_data_status" in names:
                    print("\nDATA STATUS")
                    show(await session.call_tool("cm_get_data_status", {}))
                if "cm_get_stock_quote" in names:
                    for s in SYMBOLS:
                        print("\nQUOTE", s)
                        show(await session.call_tool("cm_get_stock_quote", {"symbol": s}))
            except Exception as e:
                print("Tool call failed:", repr(e)[:500])


asyncio.run(main())
