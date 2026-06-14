"""Filter 3 — Execution and custody via Trust Wallet Agent Kit (twak CLI).

Thin adapter over the `twak` CLI (v0.17.x). The private key stays LOCAL (twak
keystore, in ~/.twak), and the signature happens locally on each swap → it meets the
self-custody criterion of the TWAK prize rubric.

Real commands used (confirmed via `twak <cmd> --help`):
  twak wallet create   --password <pw> [--no-keychain] --json
  twak wallet address  --chain bsc --json
  twak wallet portfolio --chains bsc --password <pw> --json
  twak swap <from> <to> --usd <amt> --chain bsc --slippage <pct> [--quote-only] --password <pw> --json
  twak transfer --to <addr> --amount <n> --token <assetId> --confirm-to <addr> --max-usd <n> --password <pw> --json
  twak compete register --password <pw> --json
  twak compete status   --password <pw> --json

The command SYNTAX is exact. The PARSING of the success output fields (tx hash,
price) is tolerant and must be confirmed in the integration test with credentials —
it is centralized in _extract_tx_hash / _extract_price for easy adjustment.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess

from boomerang.config import Config
from boomerang.types import ExecutionResult


class TwakError(RuntimeError):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class TwakExecutor:
    def __init__(self, config: Config, logger: logging.Logger | None = None) -> None:
        self._cfg = config
        self._log = logger or logging.getLogger("boomerang.vault.twak")
        # Allows overriding the binary and the node dir (useful on Windows / VPS).
        self._twak_bin = os.getenv("TWAK_BIN", "twak")
        self._node_dir = os.getenv("NODE_DIR")  # e.g.: C:\Program Files\nodejs
        self._chain = "bsc"
        self._base_stable = config.dev_safety["base_stable_symbol"]

    # ── CLI invocation ───────────────────────────────────────────────────────
    def _run(self, args: list[str], *, timeout: int = 120) -> dict | list:
        cmd = [self._twak_bin, *args, "--json"]
        if os.name == "nt":  # .cmd needs cmd.exe on Windows
            cmd = ["cmd", "/c", *cmd]

        env = os.environ.copy()
        if self._node_dir:
            env["PATH"] = self._node_dir + os.pathsep + env.get("PATH", "")

        self._log.debug("twak %s", " ".join(a for a in args if not a.startswith("0x")))
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        out = (proc.stdout or "").strip()
        if not out:
            raise TwakError(proc.stderr.strip() or f"twak returned empty (exit {proc.returncode}).")
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            # some commands may emit a banner before the JSON — grab the last {...}/[...] block
            data = json.loads(out[out.index("{") :]) if "{" in out else {"error": out}
        if isinstance(data, dict) and data.get("error"):
            # Log the FULL error payload (all fields: reason/route/amountOut/
            # minOut/decoded) — str(error) alone usually comes truncated ("0x...").
            try:
                self._log.warning("twak raw error: %s", json.dumps(data)[:800])
            except Exception:  # noqa: BLE001
                pass
            raise TwakError(str(data["error"]), data.get("errorCode"))
        return data

    # ── wallet / self-custody ────────────────────────────────────────────────
    def create_wallet(self, password: str, *, keychain: bool = True) -> dict:
        args = ["wallet", "create", "--password", password]
        if not keychain:
            args.append("--no-keychain")
        return self._run(args)  # type: ignore[return-value]

    def get_address(self, chain: str | None = None) -> str:
        data = self._run(["wallet", "address", "--chain", chain or self._chain,
                          "--password", self._password_or_env()])
        # likely field: "address"; tolerant of variations
        if isinstance(data, dict):
            return str(data.get("address") or data.get("value") or data)
        return str(data)

    def portfolio_usd(self, password: str, chains: str | None = None) -> float:
        """Total net worth in USD (equity) — basis of the drawdown circuit breaker."""
        data = self._run(["wallet", "portfolio", "--chains", chains or self._chain,
                          "--password", password])
        return self._extract_total_usd(data)

    # ── swap execution ───────────────────────────────────────────────────────
    def quote_swap(self, from_token: str, to_token: str, amount_usd: float,
                   slippage_pct: float | None = None) -> dict:
        slip = slippage_pct if slippage_pct is not None else self._cfg.max_slippage_pct
        data = self._run(["swap", from_token, to_token, "--usd", str(amount_usd),
                          "--chain", self._chain, "--slippage", str(slip), "--quote-only"])
        return data  # type: ignore[return-value]

    def quote(self, from_token: str, to_token: str, amount_usd: float,
              slippage_pct: float | None = None) -> dict:
        """Structured quote via the TWAK aggregator (scans V2+V3+other DEXs).

        Returns {out: float, price_impact: float|None, raw: dict}. Spends NOTHING
        (uses --quote-only). It is the source of truth for Filter 2: it reflects exactly the
        route execution will use. `from`/`to` can be a symbol OR a 0x address.
        """
        data = self.quote_swap(from_token, to_token, amount_usd, slippage_pct)
        out = self._extract_qty(data) or 0.0
        impact = None
        if isinstance(data, dict) and data.get("priceImpact") is not None:
            try:
                impact = float(str(data["priceImpact"]).replace("%", "").strip())
            except (TypeError, ValueError):
                impact = None
        return {"out": out, "price_impact": impact, "raw": data}

    def buy(self, *, to_token: str, amount_usd: float, password: str,
            slippage_pct: float | None = None) -> ExecutionResult:
        """Buys `to_token` spending `amount_usd` of the base stable (e.g.: USDT)."""
        slip = slippage_pct if slippage_pct is not None else self._cfg.max_slippage_pct
        try:
            data = self._run(["swap", self._base_stable, to_token, "--usd", str(amount_usd),
                              "--chain", self._chain, "--slippage", str(slip),
                              "--password", password])
        except TwakError as exc:
            return ExecutionResult(False, to_token, error=str(exc))
        return ExecutionResult(True, to_token,
                               tx_hash=self._extract_tx_hash(data),
                               entry_price=self._extract_price(data),
                               qty=self._extract_qty(data))

    def sell_all(self, *, token: str, amount: float, password: str,
                 slippage_pct: float | None = None) -> ExecutionResult:
        """Sells `amount` of `token` back to the base stable (exit/stop)."""
        slip = slippage_pct if slippage_pct is not None else self._cfg.max_slippage_pct
        try:
            data = self._run(["swap", str(amount), token, self._base_stable,
                              "--chain", self._chain, "--slippage", str(slip),
                              "--password", password])
        except TwakError as exc:
            return ExecutionResult(False, token, error=str(exc))
        return ExecutionResult(True, token, tx_hash=self._extract_tx_hash(data))

    # ── boomerang / withdrawal with destination lock ─────────────────────────
    def transfer_to_owner(self, *, to: str, amount: float, token: str, password: str,
                          max_usd: float | None = None) -> dict:
        """Transfers to the owner's personal wallet, with --confirm-to (anti-drain)."""
        args = ["transfer", "--to", to, "--amount", str(amount), "--token", token,
                "--confirm-to", to, "--password", password]
        if max_usd is not None:
            args += ["--max-usd", str(max_usd)]
        return self._run(args)  # type: ignore[return-value]

    # ── x402: pay endpoints (CMC data) with the agent's wallet ────────────────
    def x402_request(self, url: str, *, method: str = "POST", body: dict | None = None,
                     max_payment_atomic: str = "10000", prefer_network: str = "base",
                     prefer_asset: str | None = None, timeout: int = 60) -> dict | list:
        """Makes a request to an x402 endpoint, signing the payment if required.

        max_payment_atomic: auto-approval cap in atomic units
        (10000 = 0.01 USDC with 6 places). Uses the agent's wallet on Base.

        Note: we do NOT filter by --prefer-asset by default. The name offered by
        CMC is "USD Coin" (does not match the substring "USDC"); letting twak choose
        the asset offered on the preferred network, capped by --max-payment, is the robust approach.
        """
        args = ["x402", "request", url, "--method", method,
                "--max-payment", max_payment_atomic,
                "--prefer-network", prefer_network,
                "--yes", "--password", self._password_or_env()]
        if prefer_asset:
            args += ["--prefer-asset", prefer_asset]
        if body is not None:
            args += ["--body", json.dumps(body)]
        return self._run(args, timeout=timeout)

    def _password_or_env(self) -> str:
        return os.getenv("WALLET_PASSWORD", "")

    # ── competition registration (run ONCE before Jun 22) ─────────────────────
    def register_competition(self, password: str) -> dict:
        return self._run(["compete", "register", "--password", password])  # type: ignore[return-value]

    def competition_status(self, password: str) -> dict:
        return self._run(["compete", "status", "--password", password])  # type: ignore[return-value]

    # ── tolerant parsers (CONFIRM fields in the test with credentials) ────────
    @staticmethod
    def _extract_tx_hash(data: dict | list) -> str | None:
        if isinstance(data, dict):
            for k in ("txHash", "transactionHash", "hash", "tx"):
                if data.get(k):
                    return str(data[k])
        return None

    @staticmethod
    def _extract_price(data: dict | list) -> float | None:
        if isinstance(data, dict):
            for k in ("executionPrice", "price", "rate"):
                if data.get(k) is not None:
                    try:
                        return float(data[k])
                    except (TypeError, ValueError):
                        return None
        return None

    @staticmethod
    def _extract_qty(data: dict | list) -> float | None:
        # Real twak swap format: "output": "0.996 USDC" (number + symbol).
        if isinstance(data, dict):
            for k in ("output", "amountOut", "toAmount", "received", "outputAmount"):
                v = data.get(k)
                if v is not None:
                    try:
                        return float(str(v).split()[0])
                    except (TypeError, ValueError, IndexError):
                        continue
        return None

    @staticmethod
    def _extract_total_usd(data: dict | list) -> float:
        """Sums the portfolio's USD value. Tolerant of common formats."""
        if isinstance(data, dict):
            for k in ("totalUsd", "totalUSD", "total_value_usd", "totalValueUsd"):
                if data.get(k) is not None:
                    return float(data[k])
            items = data.get("balances") or data.get("tokens") or data.get("holdings") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        total = 0.0
        for it in items:
            if isinstance(it, dict):
                for k in ("usdValue", "valueUsd", "usd", "value_usd"):
                    if it.get(k) is not None:
                        total += float(it[k])
                        break
        return total
