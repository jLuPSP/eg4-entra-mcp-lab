import asyncio

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent


async def main() -> None:
    import os

    url = os.environ.get("MCP_URL", "https://127.0.0.1:8930/mcp")
    bearer_token = os.environ.get("MCP_BEARER_TOKEN", "local-test-token")
    client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {bearer_token}"})
    try:
        async with streamable_http_client(
            url, http_client=client
        ) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                print(initialized.protocol_version)
                print(",".join(tool.name for tool in tools.tools))
                whoami = await session.call_tool("whoami", {})
                if not whoami.content or not isinstance(whoami.content[0], TextContent):
                    raise TypeError("whoami did not return text content")
                print(whoami.content[0].text)
                inverters = await session.call_tool("list_inverters", {})
                if not inverters.content or not isinstance(inverters.content[0], TextContent):
                    raise TypeError("list_inverters did not return text content")
                print(inverters.content[0].text)
                inverter_serial = os.environ.get("MCP_INVERTER_SERIAL")
                if inverter_serial:
                    state = await session.call_tool("get_current_state", {"inverter_serial": inverter_serial})
                    if not state.content or not isinstance(state.content[0], TextContent):
                        raise TypeError("get_current_state did not return text content")
                    print(state.content[0].text)
    finally:
        await client.aclose()


asyncio.run(main())
