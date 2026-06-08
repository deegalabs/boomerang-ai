"""Captura o seu TELEGRAM_MASTER_USER_ID e grava no .env.

Passos:
  1. No Telegram, abra @boomerang_wallet_ai_bot e envie qualquer mensagem (ex.: oi).
  2. Rode: .venv\\Scripts\\python scripts\\capture_telegram_id.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from boomerang.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cfg = load_config()
    token = cfg.secrets.telegram_bot_token
    r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    if not r.get("ok"):
        print("Falha:", r)
        return 1
    users = {}
    for upd in r.get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or {}
        frm = msg.get("from") or {}
        if frm.get("id"):
            users[frm["id"]] = frm.get("username") or frm.get("first_name", "?")
    if not users:
        print("Nenhuma mensagem encontrada. Envie 'oi' para @boomerang_wallet_ai_bot e rode de novo.")
        return 1
    if len(users) > 1:
        print("Varios usuarios mandaram mensagem:", users)
        print("Defina manualmente o seu ID em .env (TELEGRAM_MASTER_USER_ID).")
        return 1

    uid, uname = next(iter(users.items()))
    print(f"Seu ID: {uid} (@{uname})")

    env = ROOT / ".env"
    text = env.read_text(encoding="utf-8")
    text = re.sub(r"TELEGRAM_MASTER_USER_ID=.*", f"TELEGRAM_MASTER_USER_ID={uid}", text)
    env.write_text(text, encoding="utf-8")
    print("Gravado em .env: TELEGRAM_MASTER_USER_ID =", uid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
