"""
Templates Manager — CRUD and normalization for Canva templates.
"""

from __future__ import annotations
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clipforge.core.db import Database
from clipforge.core.models import Template


CANVA_DESIGN_ID_REGEX = re.compile(r"/design/([A-Za-z0-9_-]+)")


def extract_canva_design_id(url: str) -> Optional[str]:
    """Extract design/template ID from a Canva design URL."""
    match = CANVA_DESIGN_ID_REGEX.search(url)
    return match.group(1) if match else None


class TemplateManager:
    def __init__(self, db: Database):
        self.db = db

    def add_template(
        self,
        name: str,
        template_url: str,
        canva_design_id: Optional[str] = None,
        placeholder_map: Optional[Dict[str, Any]] = None,
    ) -> Template:
        """Register a new Canva template in the database."""
        design_id = canva_design_id or extract_canva_design_id(template_url)

        # Check if already registered by URL
        existing = self.db.get_template_by_url(template_url)
        if existing:
            return existing

        template_id = f"tpl_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        template = Template(
            id=template_id,
            canva_template_id=design_id,
            canva_design_id=design_id,
            name=name,
            template_url=template_url,
            placeholder_map=placeholder_map,
            mapped_at=now if placeholder_map else None,
            created_at=now,
        )
        self.db.save_template(template)
        return template

    def get_template(self, template_id: str) -> Optional[Template]:
        return self.db.get_template(template_id)

    def list_templates(self) -> List[Template]:
        return self.db.list_templates()

    def update_placeholder_map(
        self,
        template_id: str,
        placeholder_map: Dict[str, Any],
    ) -> Template:
        template = self.db.get_template(template_id)
        if not template:
            raise ValueError(f"Template not found: {template_id}")

        template.placeholder_map = placeholder_map
        template.mapped_at = datetime.now(timezone.utc)
        self.db.save_template(template)
        return template

    def delete_template(self, template_id: str) -> bool:
        return self.db.delete_template(template_id)
