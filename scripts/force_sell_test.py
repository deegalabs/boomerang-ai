"""Vende de volta um token (valida o caminho de SAIDA do agente — stop/trailing).

Uso: .venv\\Scripts\\python scripts\\force_sell_test.py ETH
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boomerang.config import load_config
from boomerang.vault.twak_executor import TwakError, TwakExecutor

ROOT = Path(__file__).resolve().parent.parent
AGENT = "0xc72a37f4bb7c454Fd8a9EB629aFaEeb101F67dff"


def main() -> int:
    symbol = (sys.argv[1].upper() if len(sys.argv) > 1 else "ETH")
    cfg = load_config()
    addr = json.loads((ROOT / "data" / "eligible_tokens.json").read_text(encoding="utf-8"))["tokens"][symbol]
    ex = TwakExecutor(cfg)
    pw = cfg.secrets.wallet_password or ""

    bal = ex._run(["balance", "--address", AGENT, "--chain", "bsc", "--token", addr])
    amount = bal.get("available") if isinstance(bal, dict) else None
    print(f"== Saldo {symbol}: {amount} ==")
    if not amount or float(amount) <= 0:
        print("  sem saldo para vender"); return 1

    print(f"== Vendendo {amount} {symbol} -> USDC (saida real) ==")
    res = ex.sell_all(token=addr, amount=float(amount), password=pw)
    if res.ok:
        print(f"  [OK] tx={res.tx_hash}")
        print("  -> VENDA REAL EXECUTADA. Ciclo completo (compra+venda) validado.")
        return 0
    print(f"  [FALHOU] {res.error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
