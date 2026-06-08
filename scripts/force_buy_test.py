"""Executa a compra real de um token pelo MESMO caminho do agente (validacao + buy),
mostrando cada passo no console (o /buy do Telegram nao loga aqui).

Uso: .venv\\Scripts\\python scripts\\force_buy_test.py ETH
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boomerang.config import load_config
from boomerang.vault.bnb_validation import BNBValidator
from boomerang.vault.twak_executor import TwakError, TwakExecutor

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    symbol = (sys.argv[1].upper() if len(sys.argv) > 1 else "ETH")
    cfg = load_config()
    tokens = json.loads((ROOT / "data" / "eligible_tokens.json").read_text(encoding="utf-8"))["tokens"]
    addr = tokens.get(symbol)
    if not addr:
        print(f"{symbol} fora da whitelist"); return 1

    v = BNBValidator(cfg)
    ex = TwakExecutor(cfg)
    pw = cfg.secrets.wallet_password or ""
    size = 2.0

    print(f"== Validacao (Filtro 2) {symbol} ${size} ==")
    val = v.validate(symbol=symbol, token_address=addr, amount_usd=size)
    print(f"  ok={val.ok} | slippage={val.estimated_slippage_pct} | reason={val.reason} | {val.detail}")
    if not val.ok:
        print("  -> bloqueado na validacao."); return 1

    print(f"\n== Execucao REAL (Filtro 3) — swap USDC->{symbol} ~${size} ==")
    res = ex.buy(to_token=addr, amount_usd=size, password=pw)
    if res.ok:
        print(f"  [OK] tx={res.tx_hash}")
        print(f"       entry_price={res.entry_price} | qty={res.qty}")
        print("  -> COMPRA REAL EXECUTADA. Confira no bscscan.")
        return 0
    print(f"  [FALHOU] {res.error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
