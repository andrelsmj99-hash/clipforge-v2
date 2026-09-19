"""Tests for YouTube OAuth token health and 7-day expiration logic."""

from datetime import datetime, timedelta, timezone
import pytest
from clipforge.connectors.youtube.auth import YouTubeAuthManager
from clipforge.core.db import Database
from clipforge.core.models import Account, AccountStatus, Platform


@pytest.fixture
def auth_mgr(tmp_path):
    db = Database(db_path=tmp_path / "test_auth.db")
    return YouTubeAuthManager(db)


def test_token_health_valid():
    future_date = datetime.now(timezone.utc) + timedelta(days=5)
    acc = Account(
        id="acc_valid",
        platform=Platform.YOUTUBE,
        name="Valid Channel",
        status=AccountStatus.CONNECTED,
        token_expires_at=future_date,
    )
    health = YouTubeAuthManager.check_token_health(acc)
    assert not health["is_expired"]
    assert not health["warning"]
    assert health["days_remaining"] == pytest.approx(5.0, abs=0.1)


def test_token_health_warning():
    near_future = datetime.now(timezone.utc) + timedelta(hours=12)
    acc = Account(
        id="acc_warn",
        platform=Platform.YOUTUBE,
        name="Expiring Soon Channel",
        status=AccountStatus.CONNECTED,
        token_expires_at=near_future,
    )
    health = YouTubeAuthManager.check_token_health(acc)
    assert not health["is_expired"]
    assert health["warning"] is True
    assert health["days_remaining"] == pytest.approx(0.5, abs=0.1)


def test_token_health_expired():
    past_date = datetime.now(timezone.utc) - timedelta(hours=2)
    acc = Account(
        id="acc_exp",
        platform=Platform.YOUTUBE,
        name="Expired Channel",
        status=AccountStatus.CONNECTED,
        token_expires_at=past_date,
    )
    health = YouTubeAuthManager.check_token_health(acc)
    assert health["is_expired"] is True
    assert health["days_remaining"] == 0
