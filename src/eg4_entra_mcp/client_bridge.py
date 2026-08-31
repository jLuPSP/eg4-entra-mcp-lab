from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from dataclasses import dataclass
from typing import Any

import httpx2
import keyring
import mcp.types as types
import msal
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

SERVICE = "eg4-entra-mcp-lab"
CACHE_ACCOUNT = "client-msal-cache"
CACHE_COUNT_ACCOUNT = f"{CACHE_ACCOUNT}-count"
CACHE_CHUNK_SIZE = 1000


@dataclass(frozen=True)
class BridgeConfig:
    tenant_id: str
    client_id: str
    scope: str
    mcp_url: str
    control_scope: str | None = None

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def scopes(self) -> list[str]:
        return [self.scope, *([self.control_scope] if self.control_scope else [])]


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    count_text = keyring.get_password(SERVICE, CACHE_COUNT_ACCOUNT)
    value: str | None
    if count_text:
        try:
            count = int(count_text)
        except ValueError as exc:
            raise RuntimeError("Invalid MSAL cache chunk count in credential store") from exc
        chunks = [keyring.get_password(SERVICE, f"{CACHE_ACCOUNT}-{index}") for index in range(count)]
        if any(chunk is None for chunk in chunks):
            raise RuntimeError("Incomplete MSAL cache in credential store")
        value = "".join(chunk for chunk in chunks if chunk is not None)
    else:
        # Backward-compatible read for caches written before chunked storage.
        value = keyring.get_password(SERVICE, CACHE_ACCOUNT)
    if value:
        cache.deserialize(value)
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        serialized = cache.serialize()
        chunks = [serialized[index : index + CACHE_CHUNK_SIZE] for index in range(0, len(serialized), CACHE_CHUNK_SIZE)]
        previous_count_text = keyring.get_password(SERVICE, CACHE_COUNT_ACCOUNT)
        previous_count = int(previous_count_text) if previous_count_text else 0
        for index, chunk in enumerate(chunks):
            keyring.set_password(SERVICE, f"{CACHE_ACCOUNT}-{index}", chunk)
        keyring.set_password(SERVICE, CACHE_COUNT_ACCOUNT, str(len(chunks)))
        for index in range(len(chunks), previous_count):
            keyring.delete_password(SERVICE, f"{CACHE_ACCOUNT}-{index}")
        if keyring.get_password(SERVICE, CACHE_ACCOUNT) is not None:
            keyring.delete_password(SERVICE, CACHE_ACCOUNT)


def _app(config: BridgeConfig, cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(
        config.client_id,
        authority=config.authority,
        token_cache=cache,
    )


def _acquire_silent(config: BridgeConfig) -> str | None:
    cache = _load_cache()
    app = _app(config, cache)
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(config.scopes, account=accounts[0], force_refresh=True)
    _save_cache(cache)
    token = result.get("access_token") if result else None
    return token if isinstance(token, str) else None


def login(config: BridgeConfig, *, browser: bool = False) -> None:
    cache = _load_cache()
    app = _app(config, cache)
    if browser:
        print("Complete sign-in in the browser window. Do not paste tokens into chat.", file=sys.stderr)
        result = app.acquire_token_interactive(
            scopes=config.scopes,
            prompt="select_account",
            port=33418,
        )
    else:
        flow = app.initiate_device_flow(scopes=config.scopes)
        if "user_code" not in flow:
            raise RuntimeError("Entra did not start device-code authentication")
        verification_uri = str(flow.get("verification_uri", "https://microsoft.com/devicelogin"))
        print("Complete sign-in in the browser window. Do not paste tokens into chat.", file=sys.stderr)
        print(str(flow.get("message", "")), file=sys.stderr)
        webbrowser.open(verification_uri)
        result = app.acquire_token_by_device_flow(flow)
    if not isinstance(result.get("access_token"), str):
        raise RuntimeError(f"Entra sign-in failed ({result.get('error', 'unknown_error')})")
    _save_cache(cache)
    print("MCP client bridge login cached in the OS credential store.", file=sys.stderr)


async def run_bridge(config: BridgeConfig) -> None:
    token = await asyncio.to_thread(_acquire_silent, config)
    if not token:
        raise RuntimeError("No cached Entra session. Run eg4-client-login first.")
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    try:
        async with streamable_http_client(config.mcp_url, http_client=http_client) as (remote_read, remote_write):
            async with ClientSession(remote_read, remote_write) as remote:
                await remote.initialize()
                listed = await remote.list_tools()
                tools_by_name = {tool.name: tool for tool in listed.tools}

                async def list_tools(_: Any, __: types.PaginatedRequestParams | None) -> types.ListToolsResult:
                    return types.ListToolsResult(tools=list(tools_by_name.values()))

                async def call_tool(_: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
                    if params.name not in tools_by_name:
                        return types.CallToolResult(
                            content=[types.TextContent(type="text", text="Unknown tool")], is_error=True
                        )
                    arguments = params.arguments or {}
                    result = await remote.call_tool(params.name, arguments=arguments)
                    return types.CallToolResult(
                        content=result.content,
                        structured_content=result.structured_content,
                        is_error=result.is_error,
                    )

                local = Server(
                    "EG4 Entra Client Bridge",
                    version="0.1.0",
                    instructions="Local OAuth bridge to the Entra-protected EG4 MCP server.",
                    on_list_tools=list_tools,
                    on_call_tool=call_tool,
                )
                async with stdio_server() as (read, write):
                    await local.run(read, write, local.create_initialization_options())
    finally:
        await http_client.aclose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--control-scope")
    parser.add_argument("--mcp-url", default="https://127.0.0.1:8930/mcp")
    parser.add_argument("--browser", action="store_true", help="Use interactive browser login instead of device code")
    return parser


def _config(args: argparse.Namespace) -> BridgeConfig:
    return BridgeConfig(
        tenant_id=args.tenant_id,
        client_id=args.client_id,
        scope=args.scope,
        mcp_url=args.mcp_url,
        control_scope=args.control_scope,
    )


def login_main() -> None:
    args = _parser().parse_args()
    login(_config(args), browser=args.browser)


def bridge_main() -> None:
    args = _parser().parse_args()
    asyncio.run(run_bridge(_config(args)))
