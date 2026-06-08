"""Verifica on-chain (BSC) o symbol()/decimals() de cada token-foco.

Confirma que os enderecos do eligible_tokens.json sao mesmo os tokens esperados.
Roda com: .venv\\Scripts\\python scripts\\verify_tokens.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web3 import Web3

from boomerang.config import load_config
from boomerang.vault.bnb_validation import _ERC20_ABI

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cfg = load_config()
    w3 = Web3(Web3.HTTPProvider(cfg.bsc_rpc_url, request_kwargs={"timeout": 20}))
    data = json.loads((ROOT / "data" / "eligible_tokens.json").read_text(encoding="utf-8"))
    tokens = {**data.get("base", {}), **data.get("tokens", {})}

    ok = True
    print(f"{'ESPERADO':6} {'ON-CHAIN':10} {'DEC':3}  ENDERECO")
    for expected, addr in tokens.items():
        try:
            c = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=_ERC20_ABI)
            sym = c.functions.symbol().call()
            dec = c.functions.decimals().call()
            # tokens Binance-Peg as vezes tem prefixo; aceitamos se contiver o esperado
            match = expected.upper() in sym.upper() or sym.upper() in expected.upper()
            flag = "OK " if match else "!! "
            if not match:
                ok = False
            print(f"{flag}{expected:6} {sym:10} {dec:<3}  {addr}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"!! {expected:6} ERRO: {str(e)[:60]}  {addr}")

    print("\n" + ("[PASS] Todos os enderecos conferem on-chain." if ok
                  else "[ATENCAO] Ha divergencias — revisar antes de operar."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
