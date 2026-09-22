"""Authentication persistence and password-hashing contracts."""

from __future__ import annotations

from typing import Any

from argon2 import PasswordHasher

from src.api.service import WebApplicationService
from src.core import config


class _UserRepository:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}

    def prepare(self) -> None:
        return None

    def close(self) -> None:
        return None

    def create_user(self, user: dict[str, Any]) -> dict[str, Any]:
        self.users[user["user_id"]] = dict(user)
        return dict(user)

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        user = self.users.get(user_id)
        return dict(user) if user else None

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        return next((dict(user) for user in self.users.values() if user["email"] == email), None)

    def list_users(self) -> list[dict[str, Any]]:
        return [dict(user) for user in self.users.values()]

    def update_user(self, user_id: str, **changes: Any) -> dict[str, Any] | None:
        user = self.users.get(user_id)
        if user is None:
            return None
        user.update({key: value for key, value in changes.items() if value is not None})
        return dict(user)


def test_enabled_neo4j_auth_persists_argon2_hash(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config.settings, "neo4j_enabled", True)
    repository = _UserRepository()
    service = WebApplicationService(tmp_path / "web", neo4j_repository=repository)  # type: ignore[arg-type]

    created = service.create_user("Persistent User", "user@example.com", "SecurePassword123!")
    stored = repository.users[created.user_id]

    assert stored["password_hash"].startswith("$argon2")
    assert stored["password_hash"] != "SecurePassword123!"
    assert "password" not in stored
    assert service.authenticate("USER@example.com", "SecurePassword123!").user_id == created.user_id

    restarted = WebApplicationService(tmp_path / "restarted", neo4j_repository=repository)
    assert restarted.authenticate("user@example.com", "SecurePassword123!").user_id == created.user_id
    assert restarted.get_user_by_id(created.user_id).as_response()["email"] == "user@example.com"
    assert "password_hash" not in restarted.get_user_by_id(created.user_id).as_response()
    assert PasswordHasher().verify(stored["password_hash"], "SecurePassword123!")
