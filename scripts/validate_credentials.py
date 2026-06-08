"""Valida as credenciais reais (sem precisar da carteira ainda).

Testa: Telegram (getMe), CoinMarketCap (MCP list tools), Anthropic (lista modelos
+ veredito de teste) e TWAK (auth via `twak price`). Nao imprime segredos.
Roda com: .venv\\Scripts\\python scripts\\validate_credentials.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from boomerang.brain.cmc_analyzer import AttentionAnalyzer, CMCClient
from boomerang.config import load_config
from boomerang.vault.twak_executor import TwakError, TwakExecutor


def line(t): print("\n" + "=" * 8, t, "=" * 8)


async def main() -> int:
    cfg = load_config()

    # 1) TELEGRAM
    line("TELEGRAM getMe")
    try:
        r = httpx.get(f"https://api.telegram.org/bot{cfg.secrets.telegram_bot_token}/getMe", timeout=15)
        j = r.json()
        if j.get("ok"):
            print(f"  OK bot @{j['result']['username']} (id {j['result']['id']})")
        else:
            print("  FALHA:", j)
    except Exception as e:  # noqa: BLE001
        print("  ERRO:", e)

    # 2) ANTHROPIC — lista modelos
    line("ANTHROPIC modelos")
    chosen = cfg.secrets.llm_model
    try:
        r = httpx.get("https://api.anthropic.com/v1/models",
                      headers={"x-api-key": cfg.secrets.anthropic_api_key,
                               "anthropic-version": "2023-06-01"}, timeout=20)
        if r.status_code == 200:
            ids = [m["id"] for m in r.json().get("data", [])]
            print(f"  OK {len(ids)} modelos. Primeiros: {ids[:6]}")
            if chosen not in ids:
                pref = [m for m in ids if "sonnet" in m] or ids
                chosen = pref[0]
                print(f"  '{cfg.secrets.llm_model}' nao existe -> usar '{chosen}'")
        else:
            print("  FALHA status", r.status_code, r.text[:120])
    except Exception as e:  # noqa: BLE001
        print("  ERRO:", e)

    # 3) ANTHROPIC — veredito de teste (Filtro 1, LLM)
    line("ANTHROPIC veredito (mock metrics)")
    try:
        an = AttentionAnalyzer(cfg)
        object.__setattr__(cfg.secrets, "llm_model", chosen)  # garante modelo valido
        mock = {"search_volume_change_pct": 48, "sentiment_label": "Greed", "rsi": 58}
        v = await an.evaluate("ETH", raw_metrics=mock)
        print(f"  OK veredito: score={v.confidence_score} action={v.action.value} :: {v.rationale[:80]}")
    except Exception as e:  # noqa: BLE001
        print("  ERRO:", type(e).__name__, str(e)[:160])

    # 4) CMC — lista tools via MCP
    line("COINMARKETCAP MCP list_tools")
    try:
        names = await asyncio.wait_for(CMCClient(cfg).list_tool_names(), timeout=40)
        print(f"  OK {len(names)} tools: {names}")
    except Exception as e:  # noqa: BLE001
        print("  ERRO:", type(e).__name__, str(e)[:200])

    # 5) TWAK — auth (price exige credencial, nao a carteira)
    line("TWAK auth (price BNB)")
    try:
        data = TwakExecutor(cfg)._run(["price", "BNB", "--chain", "bsc"])
        print("  OK resposta recebida (auth valida).", str(data)[:120])
    except TwakError as e:
        print("  TwakError:", str(e)[:160])
    except Exception as e:  # noqa: BLE001
        print("  ERRO:", type(e).__name__, str(e)[:160])

    print("\n--- fim da validacao ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
