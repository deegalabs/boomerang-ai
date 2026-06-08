"""Inspeciona schemas das tools do CMC e tenta uma chamada real (custa ~x402)."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boomerang.config import load_config

WANT = ["get_crypto_quotes_latest", "get_crypto_technical_analysis",
        "trending_crypto_narratives", "get_crypto_metrics",
        "get_global_crypto_derivatives_metrics", "get_crypto_latest_news"]


async def main() -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    cfg = load_config()
    key = cfg.secrets.cmc_api_key
    url = cfg.cmc["x402_endpoint"]
    headers = {"Authorization": f"Bearer {key}", "X-CMC_PRO_API_KEY": key}

    async with streamablehttp_client(url, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            by_name = {t.name: t for t in tools}

            print("===== SCHEMAS (argumentos) =====")
            for name in WANT:
                t = by_name.get(name)
                if not t:
                    continue
                props = (t.inputSchema or {}).get("properties", {})
                required = (t.inputSchema or {}).get("required", [])
                print(f"\n# {name}\n  required={required}\n  props={list(props.keys())}")

            print("\n===== CHAMADA REAL: get_crypto_quotes_latest (ETH) =====")
            for args in ({"symbol": "ETH"}, {"symbol": "ETH", "convert": "USD"}, {"slug": "ethereum"}):
                try:
                    res = await s.call_tool("get_crypto_quotes_latest", args)
                    txt = res.structuredContent or (res.content[0].text if res.content else None)
                    print(f"  args={args} -> OK: {json.dumps(txt)[:400] if not isinstance(txt,str) else txt[:400]}")
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"  args={args} -> {type(e).__name__}: {str(e)[:150]}")


if __name__ == "__main__":
    asyncio.run(main())
