"""Cliente x402 real para a CoinMarketCap, com assinatura via BNB AI Agent SDK.

Por que isto existe e por que o twak não bastava: o endpoint da CMC é MCP e exige
o header `Accept: application/json, text/event-stream` que o cliente x402 do twak
não enviava (dava 400). Aqui controlamos o HTTP, então mandamos o header certo,
recebemos o desafio 402 (`PAYMENT-REQUIRED`), assinamos a autorização de pagamento
com o `X402Signer` do `bnbagent` e reenviamos com o header `PAYMENT-SIGNATURE`.

O esquema usado é o x402 v2 `exact` com EIP-3009 `TransferWithAuthorization`
(assinatura off-chain, gasless) em USDC na Base — a opção mais simples e a que já
temos fundos. A CMC também aceita pagamento em USDC/United Stables na BNB Chain
(permit2), exposto em `pick_accept(..., prefer_network=...)`.

Custódia: quem assina é a carteira passada ao `X402Signer` (o SDK exige que
`message['from']` seja essa carteira). Logo a carteira pagadora precisa de fato
deter o USDC. Mantemos isso fora deste módulo (o chamador injeta a carteira/signer).
"""
from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.error
import urllib.request
from typing import Any

CMC_X402_URL = "https://mcp.coinmarketcap.com/x402/mcp"
_ACCEPT = "application/json, text/event-stream"

# Ativos de pagamento que a CMC aceita (chainId, contrato, decimais). Usados para
# montar um allowlist ENXUTO na SigningPolicy do bnbagent: o agente só assina
# pagamentos para estes domínios conhecidos, nunca um contrato arbitrário.
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"   # 6 dec, chainId 8453
USDC_BSC = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"    # 18 dec, chainId 56
UNITED_STABLES_BSC = "0xcE24439F2D9C6a2289F741120FE202248B666666"  # 18 dec, chainId 56
_PAYMENT_DOMAINS = {(8453, USDC_BASE), (56, USDC_BSC), (56, UNITED_STABLES_BSC)}
# Teto por chamada (base units). Uma chamada custa ~$0.01; capamos bem acima disso
# para barrar um desafio 402 malicioso com `value` inflado.
_MAX_VALUE = {USDC_BASE: 100_000, USDC_BSC: 10**17, UNITED_STABLES_BSC: 10**17}

# EIP-3009 TransferWithAuthorization (esquema x402 "exact" em EVM).
_EIP3009_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
}


class X402Error(RuntimeError):
    """Falha no fluxo de pagamento x402 (desafio, assinatura ou liquidação)."""


def signing_policy():
    """SigningPolicy do bnbagent liberando SÓ os ativos de pagamento da CMC.

    Mantém todas as outras defesas (tipo primário, janela de validade); apenas
    estende o allowlist de domínios para os contratos USDC/United Stables conhecidos.
    """
    from web3 import Web3

    from bnbagent import SigningPolicy
    allow = {(cid, Web3.to_checksum_address(addr)) for cid, addr in _PAYMENT_DOMAINS}
    return SigningPolicy.strict_default().extend(domain_allowlist=allow)


def make_signer(wallet):
    """Embrulha uma carteira bnbagent num X402Signer com teto por chamada."""
    from bnbagent import X402Signer

    from web3 import Web3
    caps = {Web3.to_checksum_address(a): v for a, v in _MAX_VALUE.items()}
    return X402Signer(wallet, max_value_per_call=caps)


def open_signer(password: str, wallets_dir: str, address: str | None = None):
    """Abre a carteira pagadora (com a SigningPolicy da CMC) e devolve o X402Signer.

    A SigningPolicy é aplicada pela própria carteira, então precisa ser passada aqui.
    """
    from bnbagent import EVMWalletProvider
    wallet = EVMWalletProvider(password=password, persist=True, wallets_dir=wallets_dir,
                              address=address, signing_policy=signing_policy())
    return make_signer(wallet)


def _post(payload: dict, extra_headers: dict | None = None) -> tuple[int, dict, str]:
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Accept": _ACCEPT}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(CMC_X402_URL, data=body, method="POST", headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=40)
        return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def _parse_mcp(text: str) -> Any:
    """O endpoint responde JSON ou SSE (event-stream). Extrai o JSON-RPC result."""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    # SSE: linhas "data: {...}"
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    continue
    raise X402Error(f"Resposta MCP não-parseável: {text[:200]}")


def list_tools() -> list[dict]:
    """Lista os tools do MCP da CMC (gratuito, sem pagamento)."""
    st, _, body = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    if st != 200:
        raise X402Error(f"tools/list falhou: HTTP {st}")
    data = _parse_mcp(body)
    return data.get("result", {}).get("tools", [])


def decode_challenge(headers: dict) -> dict:
    """Decodifica o header PAYMENT-REQUIRED (base64 JSON) do desafio 402."""
    raw = headers.get("PAYMENT-REQUIRED") or headers.get("Payment-Required")
    if not raw:
        raise X402Error("desafio 402 sem header PAYMENT-REQUIRED")
    return json.loads(base64.b64decode(raw))


def pick_accept(challenge: dict, prefer_network: str = "eip155:8453",
                method: str = "eip3009") -> dict:
    """Escolhe a forma de pagamento. Padrão: USDC na Base via EIP-3009 (mais simples).

    Cai para a primeira opção compatível com `method` se a rede preferida não existir.
    """
    accepts = challenge.get("accepts", [])
    for a in accepts:
        if a.get("network") == prefer_network and a.get("extra", {}).get("assetTransferMethod") == method:
            return a
    for a in accepts:
        if a.get("extra", {}).get("assetTransferMethod") == method:
            return a
    if accepts:
        return accepts[0]
    raise X402Error("desafio 402 sem opções de pagamento (accepts vazio)")


def _build_payment_header(signer, challenge: dict, accept: dict, from_addr: str) -> str:
    """Assina a autorização EIP-3009 e monta o header PAYMENT-SIGNATURE (x402 v2)."""
    chain_id = int(accept["network"].split(":")[1])
    asset = accept["asset"]
    pay_to = accept["payTo"]
    value = int(accept["amount"])
    extra = accept.get("extra", {})
    now = int(time.time())
    valid_after = now - 10  # leve folga p/ clock skew; janela curta p/ a SigningPolicy
    valid_before = now + int(accept.get("maxTimeoutSeconds", 60)) + 60
    nonce = "0x" + secrets.token_bytes(32).hex()

    domain = {"name": extra.get("name", "USD Coin"), "version": extra.get("version", "2"),
              "chainId": chain_id, "verifyingContract": asset}
    message = {"from": from_addr, "to": pay_to, "value": value,
               "validAfter": valid_after, "validBefore": valid_before, "nonce": nonce}

    signed = signer.sign_payment(domain=domain, types=_EIP3009_TYPES,
                                 message=message, expected_to=pay_to)
    signature = signed.get("signature") if isinstance(signed, dict) else signed
    if hasattr(signature, "hex"):
        signature = signature.hex()
    if isinstance(signature, str) and not signature.startswith("0x"):
        signature = "0x" + signature

    # x402 v2: top-level `resource` + `accepted` (eco do requisito) + `payload`.
    envelope = {
        "x402Version": 2,
        "resource": challenge.get("resource", {}),
        "accepted": accept,
        "payload": {
            "signature": signature,
            "authorization": {
                "from": from_addr, "to": pay_to, "value": str(value),
                "validAfter": str(valid_after), "validBefore": str(valid_before),
                "nonce": nonce,
            },
        },
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def call_tool(name: str, arguments: dict, signer, *, prefer_network: str = "eip155:8453") -> dict:
    """Chama um tool pago da CMC via x402: tenta -> 402 -> assina -> reenvia.

    `signer` é um bnbagent.X402Signer cuja carteira detém o ativo de pagamento.
    Retorna {paid, status, result|error, amount, network, asset}.
    """
    rpc = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
           "params": {"name": name, "arguments": arguments}}
    st, headers, body = _post(rpc)
    if st == 200:
        return {"paid": False, "status": 200, "result": _parse_mcp(body)}
    if st != 402:
        raise X402Error(f"esperava 402, veio HTTP {st}: {body[:200]}")

    challenge = decode_challenge(headers)
    accept = pick_accept(challenge, prefer_network=prefer_network)
    header = _build_payment_header(signer, challenge, accept, signer.wallet_address)

    st2, _, body2 = _post(rpc, {"PAYMENT-SIGNATURE": header})
    out = {"paid": True, "status": st2, "amount": accept["amount"],
           "network": accept["network"], "asset": accept["asset"]}
    if st2 == 200:
        out["result"] = _parse_mcp(body2)
    else:
        out["error"] = body2[:400]
    return out
