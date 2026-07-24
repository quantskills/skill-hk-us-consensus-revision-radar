"""Environment-only PandaData authentication with process-memory token state."""

from __future__ import annotations

import getpass
import importlib
import time
from dataclasses import dataclass
from typing import Callable, Mapping


class CredentialError(ValueError):
    """Raised when required PandaData environment variables are unavailable."""


class AuthenticationError(RuntimeError):
    """Raised when PandaData rejects authentication."""


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


@dataclass
class AuthSession:
    _auth_manager: object
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        clear_auth = getattr(self._auth_manager, "clear_auth", None)
        if callable(clear_auth):
            clear_auth()
        self._closed = True

    def __enter__(self) -> "AuthSession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.close()
        return False


def normalize_username(value: str) -> str:
    username = value.strip()
    if len(username) == 11 and username.isascii() and username.isdigit():
        return f"86{username}"
    return username


def read_credentials(env: Mapping[str, str]) -> Credentials:
    username = normalize_username(env.get("PANDADATA_USERNAME", ""))
    password = env.get("PANDADATA_PASSWORD", "")
    missing = []
    if not username:
        missing.append("PANDADATA_USERNAME")
    if not password:
        missing.append("PANDADATA_PASSWORD")
    if missing:
        raise CredentialError("Missing PandaData credentials: " + ", ".join(missing))
    return Credentials(username=username, password=password)



def resolve_credentials(
    env: Mapping[str, str],
    *,
    interactive: bool,
    username_reader: Callable[[str], str] = input,
    password_reader: Callable[[str], str] = getpass.getpass,
) -> Credentials:
    """Resolve credentials from the process environment or a local terminal."""
    values = {
        "PANDADATA_USERNAME": env.get("PANDADATA_USERNAME", ""),
        "PANDADATA_PASSWORD": env.get("PANDADATA_PASSWORD", ""),
    }
    if values["PANDADATA_USERNAME"] and values["PANDADATA_PASSWORD"]:
        return read_credentials(values)
    if not interactive:
        return read_credentials(values)
    try:
        if not values["PANDADATA_USERNAME"]:
            values["PANDADATA_USERNAME"] = username_reader("PandaData Username: ")
        if not values["PANDADATA_PASSWORD"]:
            values["PANDADATA_PASSWORD"] = password_reader("PandaData Password: ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise CredentialError("PandaData credential input cancelled") from exc
    return read_credentials(values)


def _resolve_auth_manager(panda_module):
    manager = getattr(panda_module, "auth_manager", None)
    if manager is not None:
        return manager
    return importlib.import_module("panda_data.auth_manager")


def _memory_save_factory(auth_manager):
    def save_auth_state(username, password, base_url, token, expires_in=14400):
        del password
        lock = auth_manager._auth_lock
        state = auth_manager._auth_state
        with lock:
            state.token = token
            state.username = username
            state.base_url = base_url
            state.login_timestamp = time.time()
            state.token_expires_in_seconds = expires_in

    return save_auth_state


def authenticate(panda_module, credentials: Credentials) -> AuthSession:
    auth_manager = _resolve_auth_manager(panda_module)
    original_save = auth_manager.save_auth_state
    auth_manager.save_auth_state = _memory_save_factory(auth_manager)
    try:
        panda_module.init_token(
            username=credentials.username,
            password=credentials.password,
        )
    except Exception as exc:
        clear_auth = getattr(auth_manager, "clear_auth", None)
        if callable(clear_auth):
            clear_auth()
        raise AuthenticationError("PandaData 登录失败") from exc
    finally:
        auth_manager.save_auth_state = original_save
    return AuthSession(auth_manager)
