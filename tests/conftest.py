from collections.abc import Iterator

import pytest

from dora_routes.config import get_settings


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Each test reads the environment afresh, and never a developer's local `.env`."""
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
