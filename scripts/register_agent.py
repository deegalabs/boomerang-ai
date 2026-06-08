"""Registro on-chain do agente na competição (BNB HACK Track 1).

RODAR UMA VEZ antes da abertura da janela de trading (22 jun 2026).
Contrato: 0x212c61b9b72c95d95bf29cf032f5e5635629aed5 (BSC).

Sem --confirm, apenas mostra o status. Com --confirm, registra de verdade.
Uso:
  .venv\\Scripts\\python scripts\\register_agent.py            # só status
  .venv\\Scripts\\python scripts\\register_agent.py --confirm  # registra
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boomerang.config import load_config
from boomerang.vault.twak_executor import TwakError, TwakExecutor


def main() -> int:
    cfg = load_config()
    ex = TwakExecutor(cfg)
    pw = cfg.secrets.wallet_password or ""
    if not pw:
        print("[erro] WALLET_PASSWORD ausente no .env.")
        return 1

    print("== Status de registro na competicao ==")
    try:
        print(ex.competition_status(pw))
    except TwakError as exc:
        print(f"[twak] {exc}")

    if "--confirm" not in sys.argv:
        print("\n(Apenas status. Rode com --confirm para registrar de verdade.)")
        return 0

    print("\n== Registrando agente on-chain... ==")
    try:
        res = ex.register_competition(pw)
        print("[ok] Registro enviado:", res)
    except TwakError as exc:
        print(f"[erro] Falha no registro: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
