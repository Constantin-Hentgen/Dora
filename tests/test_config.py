import pytest
from pydantic import ValidationError

from loopwise.config import FRANCE_BBOX, Settings


def test_defaults_work_without_any_environment() -> None:
    s = Settings()
    assert s.brouter_url == "http://localhost:17777/brouter"
    assert (s.brouter_timeout_s, s.request_timeout_s) == (15.0, 45.0)
    assert s.segments_bbox == FRANCE_BBOX
    assert not s.uses_public_brouter


def test_bbox_from_plain_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEGMENTS_BBOX", "2.2, 48.8, 2.5, 48.9")
    assert Settings().segments_bbox == (2.2, 48.8, 2.5, 48.9)


def test_inverted_bbox_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEGMENTS_BBOX", "2.5,48.8,2.2,48.9")
    with pytest.raises(ValidationError):
        Settings()


def test_public_brouter_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROUTER_URL", "https://brouter.de/brouter")
    assert Settings().uses_public_brouter


def test_user_agent_is_generic() -> None:
    ua = Settings().http_user_agent()
    assert ua.startswith("loopwise/") and "@" not in ua
