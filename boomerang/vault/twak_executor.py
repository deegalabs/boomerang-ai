"""Filtro 3 — Execução e custódia via Trust Wallet Agent Kit (twak CLI).

Adapter fino sobre o CLI `twak` (v0.17.x). A chave privada fica LOCAL (keystore
do twak, em ~/.twak), e a assinatura acontece localmente em cada swap → cumpre o
critério de autocustódia da rubrica do prêmio TWAK.

Comandos reais usados (confirmados via `twak <cmd> --help`):
  twak wallet create   --password <pw> [--no-keychain] --json
  twak wallet address  --chain bsc --json
  twak wallet portfolio --chains bsc --password <pw> --json
  twak swap <from> <to> --usd <amt> --chain bsc --slippage <pct> [--quote-only] --password <pw> --json
  twak transfer --to <addr> --amount <n> --token <assetId> --confirm-to <addr> --max-usd <n> --password <pw> --json
  twak compete register --password <pw> --json
  twak compete status   --password <pw> --json

A SINTAXE dos comandos é exata. O PARSE dos campos de saída de sucesso (tx hash,
preço) é tolerante e deve ser confirmado no teste de integração com credenciais —
está centralizado em _extract_tx_hash / _extract_price para fácil ajuste.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

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
        # Permite sobrescrever o binário e o dir do node (útil no Windows / VPS).
        self._twak_bin = os.getenv("TWAK_BIN", "twak")
        self._node_dir = os.getenv("NODE_DIR")  # ex.: C:\Program Files\nodejs
        self._chain = "bsc"
        self._base_stable = config.dev_safety["base_stable_symbol"]

    # ── invocação do CLI ─────────────────────────────────────────────────────
    def _run(self, args: list[str], *, timeout: int = 120) -> dict | list:
        cmd = [self._twak_bin, *args, "--json"]
        if os.name == "nt":  # .cmd precisa do cmd.exe no Windows
            cmd = ["cmd", "/c", *cmd]

        env = os.environ.copy()
        if self._node_dir:
            env["PATH"] = self._node_dir + os.pathsep + env.get("PATH", "")

        self._log.debug("twak %s", " ".join(a for a in args if not a.startswith("0x")))
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        out = (proc.stdout or "").strip()
        if not out:
            raise TwakError(proc.stderr.strip() or f"twak retornou vazio (exit {proc.returncode}).")
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            # alguns comandos podem emitir banner antes do JSON — pega o último bloco {...}/[...]
            data = json.loads(out[out.index("{") :]) if "{" in out else {"error": out}
        if isinstance(data, dict) and data.get("error"):
            # Log do payload de erro COMPLETO (todos os campos: reason/route/amountOut/
            # minOut/decoded) — o str(error) sozinho costuma vir truncado ("0x...").
            try:
                self._log.warning("twak erro bruto: %s", json.dumps(data)[:800])
            except Exception:  # noqa: BLE001
                pass
            raise TwakError(str(data["error"]), data.get("errorCode"))
        return data

    # ── carteira / autocustódia ──────────────────────────────────────────────
    def create_wallet(self, password: str, *, keychain: bool = True) -> dict:
        args = ["wallet", "create", "--password", password]
        if not keychain:
            args.append("--no-keychain")
        return self._run(args)  # type: ignore[return-value]

    def get_address(self, chain: str | None = None) -> str:
        data = self._run(["wallet", "address", "--chain", chain or self._chain,
                          "--password", self._password_or_env()])
        # campo provável: "address"; tolerante a variações
        if isinstance(data, dict):
            return str(data.get("address") or data.get("value") or data)
        return str(data)

    def portfolio_usd(self, password: str, chains: str | None = None) -> float:
        """Patrimônio total em USD (equity) — base do circuit breaker de drawdown."""
        data = self._run(["wallet", "portfolio", "--chains", chains or self._chain,
                          "--password", password])
        return self._extract_total_usd(data)

    # ── execução de swaps ────────────────────────────────────────────────────
    def quote_swap(self, from_token: str, to_token: str, amount_usd: float,
                   slippage_pct: float | None = None) -> dict:
        slip = slippage_pct if slippage_pct is not None else self._cfg.max_slippage_pct
        data = self._run(["swap", from_token, to_token, "--usd", str(amount_usd),
                          "--chain", self._chain, "--slippage", str(slip), "--quote-only"])
        return data  # type: ignore[return-value]

    def quote(self, from_token: str, to_token: str, amount_usd: float,
              slippage_pct: float | None = None) -> dict:
        """Cotação estruturada via agregador do TWAK (varre V2+V3+outros DEX).

        Retorna {out: float, price_impact: float|None, raw: dict}. NÃO gasta nada
        (usa --quote-only). É a fonte de verdade do Filtro 2: reflete exatamente a
        rota que a execução vai usar. `from`/`to` podem ser símbolo OU endereço 0x.
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
        """Compra `to_token` gastando `amount_usd` da stable base (ex.: USDT)."""
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
        """Vende `amount` de `token` de volta para a stable base (saída/stop)."""
        slip = slippage_pct if slippage_pct is not None else self._cfg.max_slippage_pct
        try:
            data = self._run(["swap", str(amount), token, self._base_stable,
                              "--chain", self._chain, "--slippage", str(slip),
                              "--password", password])
        except TwakError as exc:
            return ExecutionResult(False, token, error=str(exc))
        return ExecutionResult(True, token, tx_hash=self._extract_tx_hash(data))

    # ── boomerang / saque com trava de destino ───────────────────────────────
    def transfer_to_owner(self, *, to: str, amount: float, token: str, password: str,
                          max_usd: float | None = None) -> dict:
        """Transfere para a carteira pessoal do dono, com --confirm-to (anti-drenagem)."""
        args = ["transfer", "--to", to, "--amount", str(amount), "--token", token,
                "--confirm-to", to, "--password", password]
        if max_usd is not None:
            args += ["--max-usd", str(max_usd)]
        return self._run(args)  # type: ignore[return-value]

    # ── x402: pagar endpoints (dados da CMC) com a carteira do agente ─────────
    def x402_request(self, url: str, *, method: str = "POST", body: dict | None = None,
                     max_payment_atomic: str = "10000", prefer_network: str = "base",
                     prefer_asset: str | None = None, timeout: int = 60) -> dict | list:
        """Faz uma requisição a um endpoint x402, assinando o pagamento se exigido.

        max_payment_atomic: teto de auto-aprovação em unidades atômicas
        (10000 = 0.01 USDC com 6 casas). Usa a carteira do agente na Base.

        Obs.: NÃO filtramos por --prefer-asset por padrão. O nome ofertado pela
        CMC é "USD Coin" (não casa com a substring "USDC"); deixar o twak escolher
        o ativo ofertado na rede preferida, limitado por --max-payment, é o robusto.
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

    # ── registro na competição (rodar UMA vez antes de 22/jun) ────────────────
    def register_competition(self, password: str) -> dict:
        return self._run(["compete", "register", "--password", password])  # type: ignore[return-value]

    def competition_status(self, password: str) -> dict:
        return self._run(["compete", "status", "--password", password])  # type: ignore[return-value]

    # ── parsers tolerantes (CONFIRMAR campos no teste com credenciais) ────────
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
        # Formato real do twak swap: "output": "0.996 USDC" (número + símbolo).
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
        """Soma o valor USD do portfólio. Tolerante a formatos comuns."""
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
