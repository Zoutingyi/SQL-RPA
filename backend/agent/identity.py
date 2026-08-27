"""Request-scoped actor identity for agent tool calls."""

from contextvars import ContextVar

_actor_id: ContextVar[str] = ContextVar("sql_rpa_actor_id", default="agent")
_actor_username: ContextVar[str] = ContextVar("sql_rpa_actor_username", default="agent")
_actor_role: ContextVar[str] = ContextVar("sql_rpa_actor_role", default="operator")


def set_actor(user_id: str, username: str, role: str = "operator") -> None:
    _actor_id.set(user_id)
    _actor_username.set(username)
    _actor_role.set(role)


def get_actor_id() -> str:
    return _actor_id.get()


def get_actor_username() -> str:
    return _actor_username.get()


def get_actor_role() -> str:
    return _actor_role.get()
