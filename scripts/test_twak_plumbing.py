"""Testa o ENCANAMENTO do Filtro 3 (adapter twak): invocação + parse de JSON + erro.

Sem credenciais, o twak responde um JSON de erro; o adapter deve transformá-lo
em TwakError limpo. Isso prova subprocess + parse + tratamento de erro.
Roda com: .venv\\Scripts\\python scripts\\test_twak_plumbing.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Aponta para o twak instalado e o node (esta máquina; no VPS virá do PATH).
os.environ.setdefault("TWAK_BIN", str(Path(os.environ["APPDATA"]) / "npm" / "twak.cmd"))
os.environ.setdefault("NODE_DIR", r"C:\Program Files\nodejs")

from boomerang.config import load_config
from boomerang.vault.twak_executor import TwakError, TwakExecutor


def main() -> int:
    cfg = load_config()
    ex = TwakExecutor(cfg)
    print(f"[twak] bin = {ex._twak_bin}")

    try:
        ex.competition_status(password="dummy")
        print("[twak] FALHA: esperava erro de credenciais, mas passou.")
        return 1
    except TwakError as e:
        msg = str(e)
        print(f"[twak] erro tratado corretamente: '{msg[:70]}...' (code={e.code})")
        assert e.code, msg  # JSON de erro parseado em TwakError estruturado
        print("[twak] adapter invoca o CLI, captura e parseia o JSON  OK")

    print("\n[PASS] Encanamento do Filtro 3 (adapter twak) funcionando.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
