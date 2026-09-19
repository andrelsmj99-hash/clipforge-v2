"""
Interactive Template Mapper via Playwright and Canva Apps SDK.
"""

from __future__ import annotations
import logging
import threading
import time
from typing import Any, Dict, Optional

from playwright.sync_api import sync_playwright

from clipforge.core.db import Database
from clipforge.core.models import Template
from clipforge.modules.templates.dev_server import CanvaDevServer
from clipforge.modules.templates.manager import TemplateManager
from clipforge.modules.templates.session import CanvaSessionManager

logger = logging.getLogger(__name__)


class TemplateMapper:
    def __init__(self, db: Database, dev_server: Optional[CanvaDevServer] = None):
        self.db = db
        self.manager = TemplateManager(db)
        self.session_manager = CanvaSessionManager()
        self.dev_server = dev_server or CanvaDevServer()

    def map_template(self, template_id: str, timeout_seconds: int = 180) -> Template:
        """
        Interactive 1-time placeholder mapping flow for a Canva template:
        1. Ensures dev server is running.
        2. Opens template URL in Playwright (visible browser).
        3. User clicks on video element in canvas.
        4. Canva App captures SelectionEvent and emits CLIPFORGE_PLACEHOLDER_SELECTED.
        5. Python captures reference and saves to template.placeholder_map.
        """
        template = self.manager.get_template(template_id)
        if not template:
            raise ValueError(f"Template {template_id} not found.")

        if not template.template_url:
            raise ValueError(f"Template {template_id} does not have a valid template_url.")

        # Ensure Canva App Dev Server is running
        if not self.dev_server.is_healthy():
            logger.info("Canva App dev server is not active. Attempting to start it...")
            if not self.dev_server.start(wait_timeout=15.0):
                raise RuntimeError("Could not connect to Canva App dev server on port 8080. Please run 'npm start' in canva_app/.")

        selection_received = threading.Event()
        captured_data: Dict[str, Any] = {}

        def on_selection(payload: Dict[str, Any]):
            logger.info(f"Captured Canva selection event: {payload}")
            captured_data.update(payload)
            selection_received.set()

        logger.info(f"Opening template {template.name} for interactive mapping...")
        logger.info("INSTRUCTIONS: In the opened browser window, click on the video element you want to use as a placeholder.")

        with sync_playwright() as p:
            context = self.session_manager.launch_context(p, headless=False)
            page = context.new_page() if not context.pages else context.pages[0]

            # Expose callback function for bridge
            page.expose_function("onClipforgePlaceholderSelected", on_selection)

            # Bridge postMessage from Canva App iframe to parent window
            page.add_init_script("""
                window.addEventListener("message", (event) => {
                    if (event.data && event.data.type === "CLIPFORGE_PLACEHOLDER_SELECTED") {
                        if (window.onClipforgePlaceholderSelected) {
                            window.onClipforgePlaceholderSelected(event.data);
                        }
                    }
                });
            """)

            page.goto(template.template_url, wait_until="domcontentloaded")

            # Wait for selection callback
            success = selection_received.wait(timeout=timeout_seconds)
            context.close()

            if not success:
                raise TimeoutError(f"Mapping timed out after {timeout_seconds}s. No video element was selected.")

            placeholder_ref = captured_data.get("ref", "mapped_video_slot_1")
            placeholder_map = {
                "main_video": {
                    "ref": placeholder_ref,
                    "slot_name": "video_1",
                    "mapped_at": time.time(),
                }
            }

            updated = self.manager.update_placeholder_map(template_id, placeholder_map)
            logger.info(f"Template {template.name} successfully mapped to slot: {placeholder_ref}")
            return updated
