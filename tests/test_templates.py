"""
Tests for Canva Templates Management (Módulo Templates — Seção 9 do Planejamento).
"""

from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch

from clipforge.core.db import Database
from clipforge.core.models import Template
from clipforge.modules.templates.dev_server import CanvaDevServer, is_dev_server_running
from clipforge.modules.templates.manager import TemplateManager, extract_canva_design_id


@pytest.fixture
def db(tmp_path):
    return Database(db_path=tmp_path / "test_templates.db")


def test_extract_canva_design_id():
    url1 = "https://www.canva.com/design/DAGX12345/edit"
    url2 = "https://canva.com/design/DAF_abc-XYZ/view?utm_content=..."
    url3 = "https://example.com/invalid"

    assert extract_canva_design_id(url1) == "DAGX12345"
    assert extract_canva_design_id(url2) == "DAF_abc-XYZ"
    assert extract_canva_design_id(url3) is None


def test_add_and_list_templates(db):
    mgr = TemplateManager(db)
    t1 = mgr.add_template(
        name="Shorts Template 1",
        template_url="https://www.canva.com/design/DAGX999/edit",
    )
    assert t1.id.startswith("tpl_")
    assert t1.name == "Shorts Template 1"
    assert t1.canva_design_id == "DAGX999"
    assert t1.placeholder_map is None

    # Re-adding same URL returns existing
    t2 = mgr.add_template(
        name="Duplicate URL",
        template_url="https://www.canva.com/design/DAGX999/edit",
    )
    assert t2.id == t1.id

    templates = mgr.list_templates()
    assert len(templates) == 1
    assert templates[0].id == t1.id


def test_update_placeholder_map(db):
    mgr = TemplateManager(db)
    t = mgr.add_template(
        name="Template with map",
        template_url="https://www.canva.com/design/DAGX111/edit",
    )

    placeholder_map = {
        "main_video": {
            "ref": "video_element_abc",
            "slot_name": "video_1",
        }
    }
    updated = mgr.update_placeholder_map(t.id, placeholder_map)
    assert updated.placeholder_map is not None
    assert updated.placeholder_map["main_video"]["ref"] == "video_element_abc"
    assert updated.mapped_at is not None

    reloaded = mgr.get_template(t.id)
    assert reloaded.placeholder_map["main_video"]["ref"] == "video_element_abc"


def test_delete_template(db):
    mgr = TemplateManager(db)
    t = mgr.add_template(name="To Delete", template_url="https://canva.com/design/DAGX000/edit")
    assert mgr.get_template(t.id) is not None

    deleted = mgr.delete_template(t.id)
    assert deleted is True
    assert mgr.get_template(t.id) is None


def test_dev_server_status_mock():
    with patch("socket.create_connection", return_value=MagicMock()):
        assert is_dev_server_running(port=8080) is True

    with patch("socket.create_connection", side_effect=ConnectionRefusedError):
        assert is_dev_server_running(port=8080) is False
