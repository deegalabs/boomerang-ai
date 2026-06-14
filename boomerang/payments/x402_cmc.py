"""Real x402 client for CoinMarketCap, with signing via the BNB AI Agent SDK.

Why this exists and why twak wasn't enough: the CMC endpoint is MCP and requires
the `Accept: application/json, text/event-stream` header that the twak x402 client
did not send (it returned 400). Here we control the HTTP, so we send the right header,
receive the 402 challenge (`PAYMENT-REQUIRED`), sign the payment authorization
with `bnbagent`'s `X402Signer` and resend with the `PAYMENT-SIGNATURE` header.

The scheme used is x402 v2 `exact` with EIP-3009 `TransferWithAuthorization`
(off-chain, gasless signature) in USDC on Base — the simplest option and the one we
already have funds for. CMC also accepts payment in USDC/United Stables on the BNB Chain
(permit2), exposed in `pick_accept(..., prefer_network=...)`.

Custody: the signer is the wallet passed to `X402Signer` (the SDK requires that
`message['from']` be that wallet). So the paying wallet must in fact
hold the USDC. We keep that outside this module (the caller injects the wallet/signer).
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

# Payment assets that CMC accepts (chainId, contract, decimals). Used to
# build a LEAN allowlist in bnbagent's SigningPolicy: the agent only signs
# payments to these known domains, never an arbitrary contract.
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"   # 6 dec, chainId 8453
USDC_BSC = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"    # 18 dec, chainId 56
UNITED_STABLES_BSC = "0xcE24439F2D9C6a2289F741120FE202248B666666"  # 18 dec, chainId 56
_PAYMENT_DOMAINS = {(8453, USDC_BASE), (56, USDC_BSC), (56, UNITED_STABLES_BSC)}
# Cap per call (base units). One call costs ~$0.01; we cap well above that
# to block a malicious 402 challenge with an inflated `value`.
_MAX_VALUE = {USDC_BASE: 100_000, USDC_BSC: 10**17, UNITED_STABLES_BSC: 10**17}

# EIP-3009 TransferWithAuthorization (x402 "exact" scheme on EVM).
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
    """Failure in the x402 payment flow (challenge, signature or settlement)."""


def signing_policy():
    """bnbagent SigningPolicy allowing ONLY the CMC payment assets.

    Keeps all other defenses (primary type, validity window); it only
    extends the domain allowlist to the known USDC/United Stables contracts.
    """
    from web3 import Web3

    from bnbagent import SigningPolicy
    allow = {(cid, Web3.to_checksum_address(addr)) for cid, addr in _PAYMENT_DOMAINS}
    return SigningPolicy.strict_default().extend(domain_allowlist=allow)


def make_signer(wallet):
    """Wraps a bnbagent wallet in an X402Signer with a per-call cap."""
    from bnbagent import X402Signer

    from web3 import Web3
    caps = {Web3.to_checksum_address(a): v for a, v in _MAX_VALUE.items()}
    return X402Signer(wallet, max_value_per_call=caps)


def open_signer(password: str, wallets_dir: str, address: str | None = None):
    """Opens the paying wallet (with the CMC SigningPolicy) and returns the X402Signer.

    The SigningPolicy is applied by the wallet itself, so it must be passed here.
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
    """The endpoint responds with JSON or SSE (event-stream). Extracts the JSON-RPC result."""
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
    raise X402Error(f"Unparseable MCP response: {text[:200]}")


def list_tools() -> list[dict]:
    """Lists the tools of CMC's MCP (free, no payment)."""
    st, _, body = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    if st != 200:
        raise X402Error(f"tools/list failed: HTTP {st}")
    data = _parse_mcp(body)
    return data.get("result", {}).get("tools", [])


def decode_challenge(headers: dict) -> dict:
    """Decodes the PAYMENT-REQUIRED header (base64 JSON) of the 402 challenge."""
    raw = headers.get("PAYMENT-REQUIRED") or headers.get("Payment-Required")
    if not raw:
        raise X402Error("402 challenge without PAYMENT-REQUIRED header")
    return json.loads(base64.b64decode(raw))


def pick_accept(challenge: dict, prefer_network: str = "eip155:8453",
                method: str = "eip3009") -> dict:
    """Chooses the payment method. Default: USDC on Base via EIP-3009 (simplest).

    Falls back to the first option compatible with `method` if the preferred network doesn't exist.
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
    raise X402Error("402 challenge without payment options (accepts empty)")


def _build_payment_header(signer, challenge: dict, accept: dict, from_addr: str) -> str:
    """Signs the EIP-3009 authorization and builds the PAYMENT-SIGNATURE header (x402 v2)."""
    chain_id = int(accept["network"].split(":")[1])
    asset = accept["asset"]
    pay_to = accept["payTo"]
    value = int(accept["amount"])
    extra = accept.get("extra", {})
    now = int(time.time())
    valid_after = now - 10  # slight slack for clock skew; short window for the SigningPolicy
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

    # x402 v2: top-level `resource` + `accepted` (echo of the requirement) + `payload`.
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
    """Calls a paid CMC tool via x402: tries -> 402 -> signs -> resends.

    `signer` is a bnbagent.X402Signer whose wallet holds the payment asset.
    Returns {paid, status, result|error, amount, network, asset}.
    """
    rpc = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
           "params": {"name": name, "arguments": arguments}}
    st, headers, body = _post(rpc)
    if st == 200:
        return {"paid": False, "status": 200, "result": _parse_mcp(body)}
    if st != 402:
        raise X402Error(f"expected 402, got HTTP {st}: {body[:200]}")

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
