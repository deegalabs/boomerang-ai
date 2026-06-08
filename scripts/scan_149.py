"""Varredura de liquidez dos 149 tokens elegíveis na PancakeSwap (BSC).

Para cada token: resolve endereço BSC via CMC, simula compra/venda (getAmountsOut)
e mede slippage + retenção round-trip. Classifica em LÍQUIDA / FINA / REPROVADA.
Só leitura on-chain (eth_call) — zero gás, não toca chave.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from web3 import Web3

from boomerang.config import load_config
from boomerang.vault.bnb_validation import BNBValidator

load_dotenv()
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# Lista oficial dos 149 (conforme página de regras do hackathon).
SYMBOLS = ["ETH","USDT","USDC","XRP","TRX","DOGE","ZEC","ADA","LINK","BCH","DAI","TON",
"USD1","USDe","M","LTC","AVAX","SHIB","XAUt","WLFI","H","DOT","UNI","ASTER","DEXE","USDD",
"ETC","AAVE","ATOM","U","STABLE","FIL","INJ","币安人生","NIGHT","FET","TUSD","BONK","PENGU",
"CAKE","SIREN","LUNC","ZRO","KITE","FDUSD","BEAT","PIEVERSE","BTT","NFT","EDGE","FLOKI","LDO",
"B","FF","PENDLE","NEX","STG","AXS","TWT","HOME","RAY","COMP","GWEI","XCN","GENIUS","XPL","BAT",
"SKYAI","APE","IP","SFP","TAG","NXPC","AB","SAHARA","1INCH","CHEEMS","BANANAS31","RIVER","MYX",
"RAVE","SNX","FORM","LAB","HTX","USDf","CTM","BDX","SLX","UB","DUCKY","FRAX","BILL","WFI","KOGE",
"ALE","FRXUSD","USDF","GOMINING","VCNT","GUA","DUSD","SMILEK","0G","BEAM","MY","SOON","REAL","Q",
"AIOZ","ZIG","YFI","TAC","lisUSD","CYS","ZAMA","TRIA","HUMA","PLUME","ZIL","XPR","ZETA","BabyDoge",
"NILA","ROSE","VELO","UAI","BRETT","OPEN","BSB","TOSHI","BAS","ACH","AXL","LUR","ELF","KAVA","APR",
"IRYS","EURI","XUSD","BARD","DUSK","SUSHI","PEAQ","COAI","BDCA","XAUM"]

# Stablecoins / lastreadas — caixa, não alvo de trade (não faz sentido medir momentum).
STABLES = {"USDT","USDC","DAI","USD1","USDe","USDD","TUSD","FDUSD","FRAX","FRXUSD","frxUSD",
           "USDf","USDF","lisUSD","DUSD","EURI","XUSD","STABLE","BILL","BDCA","XAUt","XAUM","M"}

CMC_KEY = os.getenv("CMC_API_KEY")


def cmc_bsc_addresses(symbols: list[str]) -> dict[str, str]:
    """symbol -> endereço BSC (BEP20). Resolve em lotes pela CMC."""
    out: dict[str, str] = {}
    CHUNK = 40
    for i in range(0, len(symbols), CHUNK):
        chunk = [s for s in symbols[i:i + CHUNK]]
        q = urllib.parse.quote(",".join(chunk))
        url = f"https://pro-api.coinmarketcap.com/v2/cryptocurrency/info?symbol={q}"
        req = urllib.request.Request(url, headers={"X-CMC_PRO_API_KEY": CMC_KEY})
        try:
            data = json.load(urllib.request.urlopen(req, timeout=30)).get("data", {})
        except Exception as exc:  # noqa: BLE001
            print(f"  [CMC] lote {i} falhou: {str(exc)[:80]}")
            time.sleep(2)
            continue
        for sym, entries in data.items():
            entries = entries if isinstance(entries, list) else [entries]
            for e in entries:
                hit = None
                for c in (e.get("contract_address") or []):
                    plat = (c.get("platform") or {}).get("name", "")
                    if "BNB Smart Chain" in plat or "BNB Chain" in plat or "BSC" in plat:
                        hit = c["contract_address"]
                        break
                if hit and sym not in out:
                    out[sym] = hit
                    break
        time.sleep(1.5)  # respeita rate limit
    return out


def _best_buy(v: BNBValidator, token: str, amount_in: int):
    """Tenta caminho direto USDT->token e via WBNB; devolve (saida, caminho) melhor."""
    paths = [[v._usdt, token], [v._usdt, v._wbnb, token]]
    best, best_path = None, None
    for p in paths:
        try:
            out = v._amounts_out(amount_in, p)[-1]
            if out > 0 and (best is None or out > best):
                best, best_path = out, p
        except Exception:  # noqa: BLE001
            continue
    return best, best_path


def probe(v: BNBValidator, token: str, usd: float) -> tuple[float, float] | None:
    """Retorna (slippage_pct, retencao_roundtrip_pct) para ordem de `usd`, melhor rota."""
    udec = v._decimals(v._usdt)
    amount_in = int(usd * 10 ** udec)
    unit = 10 ** udec
    buy, path = _best_buy(v, token, amount_in)
    if not buy:
        return None
    ref, _ = _best_buy(v, token, unit)
    if not ref:
        return None
    rate_ref = ref / unit
    rate_eff = buy / amount_in
    impact = max((rate_ref - rate_eff) / rate_ref * 100.0, 0.0)
    try:
        back = v._amounts_out(buy, list(reversed(path)))[-1]
    except Exception:  # noqa: BLE001
        return impact, 0.0
    retention = back / amount_in * 100.0
    return impact, retention


def main() -> int:
    cfg = load_config()
    v = BNBValidator(cfg)
    if not v.is_connected():
        print("[ERRO] sem conexao BSC")
        return 1

    tradeable = [s for s in SYMBOLS if s not in STABLES]
    print(f"Resolvendo enderecos BSC na CMC ({len(tradeable)} nao-stables)...")
    addrs = cmc_bsc_addresses(tradeable)
    # Override com enderecos JA VERIFICADOS on-chain (Binance-Peg liquido) das 15 atuais.
    verified = json.loads((Path("data/eligible_tokens.json")).read_text(encoding="utf-8")).get("tokens", {})
    addrs.update(verified)
    print(f"  {len(addrs)} resolvidos ({len(verified)} usando enderecos verificados do nosso arquivo).\n")

    liquida, fina, reprovada, sem_contrato = [], [], [], []
    for sym in tradeable:
        addr = addrs.get(sym)
        if not addr:
            sem_contrato.append(sym)
            continue
        try:
            a = Web3.to_checksum_address(addr)
            r50 = probe(v, a, 50.0)
            if r50 and r50[0] <= 0.5 and r50[1] >= 99.0:
                liquida.append((sym, r50[0], r50[1]))
                tag = "OK-50"
            else:
                r5 = probe(v, a, 5.0)
                if r5 and r5[0] <= 0.5 and r5[1] >= 99.0:
                    fina.append((sym, r5[0], r5[1]))
                    tag = "FINA-5"
                else:
                    ref = r50 or r5
                    reprovada.append((sym, ref[0] if ref else None, ref[1] if ref else None))
                    tag = "REPROVA"
            print(f"  {sym:<12} {tag}")
        except Exception as exc:  # noqa: BLE001
            reprovada.append((sym, None, None))
            print(f"  {sym:<12} REPROVA (sem pool: {str(exc)[:40]})")
        time.sleep(0.05)

    cur = ["ETH","XRP","ADA","DOGE","LINK","LTC","AVAX","DOT","UNI","AAVE","ATOM","BCH","SHIB","FLOKI","TWT"]
    print("\n" + "=" * 60)
    print("LIQUIDA (absorve $50, slippage<=0.5%, round-trip>=99%):")
    for s, sl, rt in sorted(liquida, key=lambda x: x[1]):
        star = " *(ja na lista)" if s in cur else ""
        print(f"  {s:<12} slip {sl:.3f}%  rt {rt:.2f}%{star}")
    print(f"\nFINA (so aguenta ~$5):")
    for s, sl, rt in sorted(fina, key=lambda x: x[1]):
        star = " *(ja na lista)" if s in cur else ""
        print(f"  {s:<12} slip {sl:.3f}%  rt {rt:.2f}%{star}")
    print(f"\nREPROVADA: {len(reprovada)}  |  SEM CONTRATO BSC: {len(sem_contrato)}")
    print(f"\nRESUMO: liquidas={len(liquida)} finas={len(fina)} reprovadas={len(reprovada)} sem_contrato={len(sem_contrato)}")
    novas = [s for s, _, _ in liquida if s not in cur]
    perdidas = [s for s in cur if s not in [x[0] for x in liquida]]
    print(f"\nNOVAS candidatas liquidas (fora das 15 atuais): {novas}")
    print(f"Das 15 atuais que NAO passaram no teste $50: {perdidas}")

    Path("data/scan_149_result.json").write_text(json.dumps({
        "liquida": liquida, "fina": fina,
        "reprovada": [s for s, _, _ in reprovada], "sem_contrato": sem_contrato,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[ok] salvo em data/scan_149_result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
