import socket
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def guard(*args: object, **kwargs: object) -> None:
        raise RuntimeError("сеть в тестах запрещена")

    monkeypatch.setattr(socket, "create_connection", guard)
    yield
