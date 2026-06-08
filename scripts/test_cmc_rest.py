"""Testa a API REST da CMC (mesma key, sem x402) — fonte de dados confiavel."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from boomerang.config import load_config

BASE = "https://pro-api.coinmarketcap.com"


def main() -> int:
    cfg = load_config()
    h = {"X-CMC_PRO_API_KEY": cfg.secrets.cmc_api_key, "Accept": "application/json"}

    print("== quotes/latest (ETH id=1027) ==")
    r = httpx.get(f"{BASE}/v2/cryptocurrency/quotes/latest", params={"id": "1027"}, headers=h, timeout=20)
    print("status", r.status_code)
    if r.status_code == 200:
        q = r.json()["data"]["1027"]["quote"]["USD"]
        print(f"  ETH ${q['price']:.2f} | vol24h ${q['volume_24h']:,.0f} | "
              f"1h {q['percent_change_1h']:.2f}% | 24h {q['percent_change_24h']:.2f}% | "
              f"vol_change_24h {q.get('volume_change_24h')}")
    else:
        print("  body:", r.text[:200])

    print("\n== global-metrics ==")
    r2 = httpx.get(f"{BASE}/v1/global-metrics/quotes/latest", headers=h, timeout=20)
    print("status", r2.status_code)
    if r2.status_code == 200:
        d = r2.json()["data"]
        print(f"  BTC dominance {d.get('btc_dominance'):.1f}% | "
              f"mktcap ${d['quote']['USD']['total_market_cap']:,.0f}")
    else:
        print("  body:", r2.text[:200])

    print("\n== trending (pode exigir plano pago) ==")
    r3 = httpx.get(f"{BASE}/v1/cryptocurrency/trending/latest", headers=h, timeout=20)
    print("status", r3.status_code, "(401/403 = plano nao cobre; sem problema)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
