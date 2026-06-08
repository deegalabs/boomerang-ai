"""Valida o leitor de composição da carteira (wallet_breakdown) contra a carteira real on-chain."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boomerang.config import load_config
from boomerang.vault.bnb_validation import BNBValidator


def main() -> int:
    cfg = load_config()
    v = BNBValidator(cfg)
    if not v.is_connected():
        print("[ERRO] sem conexao BSC")
        return 1
    addr = sys.argv[1] if len(sys.argv) > 1 else "0xc72a37f4bb7c454Fd8a9EB629aFaEeb101F67dff"
    print(f"Lendo carteira on-chain: {addr}\n")
    data = v.wallet_breakdown(addr)
    print(f"{'MOEDA':<8}{'TIPO':<8}{'QTDE':>18}{'PRECO':>14}{'VALOR':>12}{'%':>8}")
    for h in data["holdings"]:
        print(f"{h['symbol']:<8}{h['kind']:<8}{h['balance']:>18.8f}"
              f"{h['price_usd']:>14.6f}{h['value_usd']:>12.4f}{h['pct']:>7.1f}%")
    print("-" * 68)
    print(f"{'TOTAL on-chain:':<48}${data['total_usd']:>11.4f}")
    print(f"\n{len(data['holdings'])} moeda(s) com saldo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
