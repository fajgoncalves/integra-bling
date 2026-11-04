"""Senhas, sessão e cifragem dos segredos (tokens e client secret do Bling)."""
import os
import hmac
import json
import time
import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from db import SessionLocal, Config

_CHAVE = None


def segredo_base() -> str:
    """SECRET_KEY do ambiente; sem ela, uma chave gerada e guardada no banco (aviso na tela de Configurações)."""
    global _CHAVE
    if _CHAVE:
        return _CHAVE
    k = os.environ.get("SECRET_KEY", "").strip()
    if not k:
        with SessionLocal() as s:
            row = s.get(Config, "secret_key_gerada")
            if not row:
                row = Config(chave="secret_key_gerada", valor=secrets.token_urlsafe(48))
                s.add(row)
                s.commit()
            k = row.valor
    _CHAVE = k
    return k


def _fernet():
    d = hashlib.sha256(("fernet:" + segredo_base()).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(d))


def cifrar(txt: str) -> str:
    return _fernet().encrypt(txt.encode()).decode() if txt else ""


def decifrar(txt: str) -> str:
    if not txt:
        return ""
    try:
        return _fernet().decrypt(txt.encode()).decode()
    except InvalidToken:
        return ""


def hash_senha(senha: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${h}"


def confere_senha(senha: str, guardado: str) -> bool:
    try:
        _, salt, _h = guardado.split("$", 2)
    except ValueError:
        return False
    return hmac.compare_digest(hash_senha(senha, salt), guardado)


def assinar(dados: dict, validade_s: int) -> str:
    corpo = base64.urlsafe_b64encode(json.dumps({**dados, "exp": int(time.time()) + validade_s}).encode()).decode()
    sig = hmac.new(segredo_base().encode(), corpo.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{corpo}.{sig}"


def verificar(tok: str) -> dict | None:
    try:
        corpo, sig = (tok or "").rsplit(".", 1)
    except ValueError:
        return None
    ok = hmac.new(segredo_base().encode(), corpo.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(ok, sig):
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(corpo.encode()))
    except ValueError:
        return None
    return d if d.get("exp", 0) >= time.time() else None


def get_config(s, chave, padrao=""):
    row = s.get(Config, chave)
    return row.valor if row else padrao


def set_config(s, chave, valor):
    row = s.get(Config, chave)
    if row:
        row.valor = valor
    else:
        s.add(Config(chave=chave, valor=valor))


def configs(s, prefixo):
    return {r.chave: r.valor for r in s.scalars(select(Config).where(Config.chave.like(prefixo + "%"))).all()}
