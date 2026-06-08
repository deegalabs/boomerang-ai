"""Teste de rede do Filtro 2 contra a BSC mainnet (somente leitura, custo zero).

Usa WBNB (endereço canônico, mundialmente conhecido) só para provar conectividade
e a matemática do validador. Roda com: .venv\\Scripts\\python scripts\\test_bnb_connection.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boomerang.config import load_config
from boomerang.vault.bnb_validation import BNBValidator

# WBNB canônico na BSC — usado apenas para o teste de leitura.
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"


def main() -> int:
    cfg = load_config()
    v = BNBValidator(cfg)

    print(f"[rpc] {cfg.bsc_rpc_url}")
    if not v.is_connected():
        print("[rpc] FALHA de conexao ou chain_id incorreto.")
        return 1
    print(f"[rpc] conectado | chain_id={v.w3.eth.chain_id} | bloco={v.w3.eth.block_number}  OK")

    dec = v._decimals(WBNB)
    print(f"[erc20] WBNB decimals = {dec}  OK")

    price = v.onchain_price_usd(WBNB)
    print(f"[preco] WBNB ~= ${price:,.2f} USDT (on-chain)  OK")

    # whitelist vazia → deve rejeitar
    r0 = v.validate(symbol="WBNB", token_address=WBNB, amount_usd=20.0)
    assert not r0.ok and r0.reason.value == "REJECTED_NOT_WHITELISTED", r0
    print(f"[whitelist] rejeita token nao-elegivel ({r0.reason.value})  OK")

    # injeta WBNB na whitelist SO PARA TESTE e roda o pipeline completo
    v._whitelist.add(v.w3.to_checksum_address(WBNB))
    r1 = v.validate(symbol="WBNB", token_address=WBNB, amount_usd=20.0, cmc_price_usd=price)
    print(f"[validate] ok={r1.ok} | slippage={r1.estimated_slippage_pct:.4f}% | "
          f"divergencia={r1.oracle_divergence_pct:.4f}% | detalhe='{r1.detail}'")
    assert r1.ok, f"WBNB deveria passar (liquidez profunda): {r1.detail}"
    assert r1.min_out and r1.min_out > 0
    print(f"[validate] expected_out={r1.expected_out} min_out={r1.min_out}  OK")

    # dessincronizacao de oraculo: preco CMC 10% diferente -> deve rejeitar
    r2 = v.validate(symbol="WBNB", token_address=WBNB, amount_usd=20.0, cmc_price_usd=price * 1.10)
    assert not r2.ok and r2.reason.value == "REJECTED_ORACLE_DESYNC", r2
    print(f"[oraculo] rejeita divergencia de 10% ({r2.oracle_divergence_pct:.2f}%)  OK")

    print("\n[PASS] Filtro 2 validado contra a BSC mainnet real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
