"""Redireciona o estado da persistência para um diretório temporário descartável.

Importe este módulo NO TOPO dos scripts de teste, ANTES de qualquer import de
``boomerang``:

    import _isolate_state  # noqa: F401  (deve vir antes de importar boomerang)

Ele seta ``BOOMERANG_STATE_DIR`` para um tempdir, de modo que
``save_state``/``append_trade`` escrevam ali em vez do estado REAL de produção
(``state/agent_state.json``). Sem isso, rodar a suíte enquanto o agente está em
produção sobrescreve posições reais. O tempdir é removido ao final do processo.
"""
import atexit
import os
import shutil
import tempfile

_DIR = tempfile.mkdtemp(prefix="boomerang_test_state_")
os.environ["BOOMERANG_STATE_DIR"] = _DIR


@atexit.register
def _cleanup() -> None:
    shutil.rmtree(_DIR, ignore_errors=True)
