"""Authentification locale et sessions opaques, sans secrets dans le navigateur."""

import math
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import bcrypt
import yaml


class AuthConfigError(RuntimeError):
    pass


def load_auth_config(path: Path) -> dict[str, Any]:
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        users = config["credentials"]["usernames"]
        cookie = config["cookie"]
        days = float(cookie["expiry_days"])
        if not users or not math.isfinite(days) or days <= 0:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cookie["name"]):
            raise ValueError
        for username, user in users.items():
            if not isinstance(username, str) or not username:
                raise ValueError
            password = user["password"]
            if not isinstance(password, str) or not re.fullmatch(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}", password):
                raise ValueError
            # Vérifie aussi la validité du coût bcrypt, sans afficher le hash.
            bcrypt.checkpw(b"configuration-check", password.encode())
        return config
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AuthConfigError(
            "Configurez les utilisateurs et leurs mots de passe bcrypt dans config.yaml (voir README)."
        ) from exc


def verify_credentials(config: dict, username: str, password: str) -> tuple[str, str] | None:
    users = config["credentials"]["usernames"]
    user = users.get(username)
    # Effectue une vérification même pour un identifiant inconnu.
    candidate = user or next(iter(users.values()))
    try:
        valid = bcrypt.checkpw(password.encode("utf-8"), candidate["password"].encode())
    except ValueError:
        valid = False
    if user and valid:
        return username, str(user.get("name") or username)
    return None


@dataclass(frozen=True)
class Session:
    username: str
    name: str
    csrf_token: str
    expires_at: float


class SessionStore:
    """Les sessions sont révoquées au redémarrage et ne sont jamais persistées."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def create(self, username: str, name: str, expiry_days: float) -> tuple[str, Session]:
        now = time.time()
        session = Session(username, name, secrets.token_urlsafe(32), now + expiry_days * 86400)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions = {key: value for key, value in self._sessions.items() if value.expires_at > now}
            self._sessions[token] = session
        return token, session

    def get(self, token: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(token)
            if session and session.expires_at <= time.time():
                self._sessions.pop(token, None)
                return None
            return session

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)
