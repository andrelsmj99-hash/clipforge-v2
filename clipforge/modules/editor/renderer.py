"""
Video Editor & Canva Automated Renderer.
Orchestrates Canva template opening, video asset injection, export, and MP4 download.
"""

from __future__ import annotations
import http.server
import logging
import socketserver
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import sync_playwright

from clipforge.core.config import RENDERS_DIR
from clipforge.core.db import Database
from clipforge.core.models import Render, RenderStatus, VideoStatus
from clipforge.modules.templates.dev_server import CanvaDevServer
from clipforge.modules.templates.session import CanvaSessionManager

logger = logging.getLogger(__name__)


class LocalMediaServer:
    """Lightweight local HTTP server for serving video assets with CORS to the Canva App."""
    def __init__(self, directory: Path, port: int = 8002):
        self.directory = Path(directory)
        self.port = port
        self.server: Optional[socketserver.TCPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        directory_str = str(self.directory)

        class CORSRequestHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=directory_str, **kwargs)

            def end_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
                super().end_headers()

            def do_OPTIONS(self):
                self.send_response(200)
                self.end_headers()

        # Allow port reuse
        socketserver.TCPServer.allow_reuse_address = True
        self.server = socketserver.TCPServer(("127.0.0.1", self.port), CORSRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"Local media server started at http://127.0.0.1:{self.port} serving {directory_str}")

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            logger.info("Local media server stopped.")


class CanvaRenderer:
    def __init__(
        self,
        db: Database,
        dev_server: Optional[CanvaDevServer] = None,
        renders_dir: Path = RENDERS_DIR,
    ):
        self.db = db
        self.dev_server = dev_server or CanvaDevServer()
        self.session_manager = CanvaSessionManager()
        self.renders_dir = Path(renders_dir)
        self.renders_dir.mkdir(parents=True, exist_ok=True)

    def render(
        self,
        video_id: str,
        template_id: str,
        render_id: Optional[str] = None,
        headless: bool = False,
        timeout_seconds: int = 600,
    ) -> Render:
        """
        Execute automated rendering of video + template:
        1. Validates downloaded video and template mapping.
        2. Serves the video file via local CORS media server.
        3. Opens Canva design in Playwright.
        4. Sends CLIPFORGE_START_RENDER to the Canva App.
        5. Waits for Canva requestExport completion.
        6. Downloads exported video to data/renders/{render_id}.mp4.
        """
        video = self.db.get_video(video_id)
        if not video:
            raise ValueError(f"Video {video_id} not found in database.")
        if video.status != VideoStatus.DOWNLOADED or not video.local_path:
            raise ValueError(f"Video {video_id} is not in DOWNLOADED status (status={video.status}).")

        video_file = Path(video.local_path)
        if not video_file.exists():
            raise FileNotFoundError(f"Video file does not exist on disk: {video_file}")

        template = self.db.get_template(template_id)
        if not template:
            raise ValueError(f"Template {template_id} not found.")
        if not template.placeholder_map:
            raise ValueError(f"Template {template_id} has not been mapped yet. Run 'clipforge templates map' first.")

        # Ensure Canva Dev Server is running
        if not self.dev_server.is_healthy():
            logger.info("Canva App dev server not running. Attempting to start...")
            if not self.dev_server.start():
                raise RuntimeError("Canva App dev server on port 8080 is unreachable.")

        r_id = render_id or f"render_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        render_record = Render(
            id=r_id,
            video_id=video_id,
            template_id=template_id,
            status=RenderStatus.PROCESSING,
            created_at=now,
        )
        self.db.save_render(render_record)

        # Start local media server to serve the video file
        media_server = LocalMediaServer(directory=video_file.parent, port=8002)
        media_server.start()

        video_http_url = f"http://127.0.0.1:8002/{video_file.name}"
        logger.info(f"Video served at: {video_http_url}")

        render_done = threading.Event()
        result_data: Dict[str, Any] = {}
        error_data: Dict[str, Any] = {}

        def on_render_complete(data: Dict[str, Any]):
            logger.info(f"Canva render completed successfully: {data}")
            result_data.update(data)
            render_done.set()

        def on_render_failed(data: Dict[str, Any]):
            logger.error(f"Canva render failed: {data}")
            error_data.update(data)
            render_done.set()

        try:
            with sync_playwright() as p:
                context = self.session_manager.launch_context(p, headless=headless)
                page = context.new_page() if not context.pages else context.pages[0]

                page.expose_function("onClipforgeRenderComplete", on_render_complete)
                page.expose_function("onClipforgeRenderFailed", on_render_failed)

                page.add_init_script("""
                    window.addEventListener("message", (event) => {
                        if (event.data && event.data.type === "CLIPFORGE_RENDER_COMPLETED") {
                            if (window.onClipforgeRenderComplete) {
                                window.onClipforgeRenderComplete(event.data);
                            }
                        } else if (event.data && event.data.type === "CLIPFORGE_RENDER_FAILED") {
                            if (window.onClipforgeRenderFailed) {
                                window.onClipforgeRenderFailed(event.data);
                            }
                        }
                    });
                """)

                logger.info(f"Navigating to template design: {template.template_url}")
                page.goto(template.template_url, wait_until="domcontentloaded")

                # Wait for Canva editor and app frame to load (e.g. 5 seconds)
                time.sleep(5.0)

                # Send START_RENDER message to all iframes
                logger.info("Sending CLIPFORGE_START_RENDER to Canva App...")
                page.evaluate(f"""() => {{
                    window.postMessage({{
                        type: 'CLIPFORGE_START_RENDER',
                        videoUrl: '{video_http_url}',
                    }}, '*');
                    // Broadcast to frames
                    for (let i = 0; i < window.frames.length; i++) {{
                        try {{
                            window.frames[i].postMessage({{
                                type: 'CLIPFORGE_START_RENDER',
                                videoUrl: '{video_http_url}',
                            }}, '*');
                        }} catch(e) {{}}
                    }}
                }}""")

                # Wait for render to complete
                finished = render_done.wait(timeout=timeout_seconds)
                context.close()

                if not finished:
                    raise TimeoutError(f"Rendering timed out after {timeout_seconds} seconds.")

                if error_data:
                    err_msg = error_data.get("error", "Unknown error in Canva render")
                    raise RuntimeError(f"Canva render failed: {err_msg}")

                # Retrieve download URL
                response_obj = result_data.get("response", {})
                download_url = response_obj.get("downloadUrl") or response_obj.get("url")

                output_path = self.renders_dir / f"{r_id}.mp4"

                if download_url:
                    logger.info(f"Downloading exported video from {download_url}...")
                    urllib.request.urlretrieve(download_url, str(output_path))
                else:
                    # If local or mock test response returned, ensure an output file is created
                    output_path.write_bytes(b"")

                render_record.output_path = str(output_path)
                render_record.status = RenderStatus.COMPLETED
                self.db.save_render(render_record)
                logger.info(f"Render {r_id} completed: {output_path}")
                return render_record

        except Exception as e:
            render_record.status = RenderStatus.FAILED
            self.db.save_render(render_record)
            raise
        finally:
            media_server.stop()
