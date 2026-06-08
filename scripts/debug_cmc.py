"""Debug da conexao com o CMC MCP — desempacota o ExceptionGroup e sonda o HTTP cru."""
import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from boomerang.config import load_config


def probe_http(url: str, key: str) -> None:
    print(f"\n--- HTTP cru: {url} ---")
    # tentativa de initialize JSON-RPC (MCP streamable http usa POST)
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18",
                       "capabilities": {}, "clientInfo": {"name": "boomerang", "version": "0.1"}}}
    for hdrs, label in [
        ({"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream",
          "Content-Type": "application/json"}, "Bearer"),
        ({"X-CMC_PRO_API_KEY": key, "Accept": "application/json, text/event-stream",
          "Content-Type": "application/json"}, "X-CMC_PRO_API_KEY"),
    ]:
        try:
            r = httpx.post(url, json=body, headers=hdrs, timeout=20)
            print(f"  [{label}] POST status={r.status_code} body={r.text[:200]}")
        except Exception as e:  # noqa: BLE001
            print(f"  [{label}] POST erro: {type(e).__name__}: {e}")
    try:
        r = httpx.get(url, headers={"X-CMC_PRO_API_KEY": key,
                                    "Accept": "application/json, text/event-stream"}, timeout=20)
        print(f"  GET status={r.status_code} body={r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"  GET erro: {type(e).__name__}: {e}")


async def probe_mcp(url: str, key: str) -> None:
    print(f"\n--- MCP client: {url} ---")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    headers = {"Authorization": f"Bearer {key}", "X-CMC_PRO_API_KEY": key}
    try:
        async with streamablehttp_client(url, headers=headers) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                print("  OK tools:", [t.name for t in tools.tools])
    except BaseException as e:  # noqa: BLE001
        print("  Falhou. Sub-excecoes:")
        if isinstance(e, BaseExceptionGroup):
            for sub in e.exceptions:
                print("   -", type(sub).__name__, str(sub)[:200])
        else:
            traceback.print_exc()


async def main() -> None:
    cfg = load_config()
    key = cfg.secrets.cmc_api_key or ""
    print("CMC key presente:", bool(key))
    for url in [cfg.cmc["mcp_endpoint"], cfg.cmc["x402_endpoint"]]:
        probe_http(url, key)
    await probe_mcp(cfg.cmc["mcp_endpoint"], key)


if __name__ == "__main__":
    asyncio.run(main())
