"""Tests for Shorts vs Long video classification logic."""

import pytest
from clipforge.core.models import VideoKind
from clipforge.modules.youtube.scraper import classify_video_kind


def test_classify_by_shorts_url():
    assert classify_video_kind(url="https://www.youtube.com/shorts/abcdef12345") == VideoKind.SHORT
    assert classify_video_kind(url="https://youtube.com/shorts/xyz") == VideoKind.SHORT


def test_classify_by_duration_and_aspect_ratio():
    # Vertical video <= 60s
    assert classify_video_kind(duration=45.0, width=1080, height=1920) == VideoKind.SHORT
    # 15s clip
    assert classify_video_kind(duration=15.0) == VideoKind.SHORT
    # Long video (2 minutes)
    assert classify_video_kind(duration=120.0, width=1920, height=1080) == VideoKind.LONG
    # Exactly 60s
    assert classify_video_kind(duration=60.0) == VideoKind.SHORT
    # 61s video -> LONG
    assert classify_video_kind(duration=61.0) == VideoKind.LONG


def test_classify_unknown():
    assert classify_video_kind(duration=None, url="https://www.youtube.com/watch?v=123") == VideoKind.UNKNOWN
