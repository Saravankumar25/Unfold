"""HTML → PDF with headless Chromium (LLD §19). Runs in the worker `export` queue.

Isolation of the page:

* the context is offline, service workers are blocked and every request is routed through `_block_network`, which
  aborts everything except `data:` / `about:blank` — nothing is fetched while rendering;
* a Content-Security-Policy is injected into the document that allows only the vendored Mermaid bundle (by its
  SHA-256 hash) to execute — the HTML itself never contains scripts (the templates escape all repository text), and
  the CSP guarantees that even a templating bug could not run anything else;
* Mermaid runs with `securityLevel: 'strict'`; a diagram that fails (or times out) is replaced by its source text in
  a styled box — one bad diagram never breaks the PDF.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_lib
import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Playwright, Route, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from unfold.core.errors import ExportError
from unfold.core.logging import get_logger
from unfold.observability.tracing import span

log = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
MERMAID_JS = STATIC_DIR / "mermaid.min.js"
PDF_UNAVAILABLE = "PDF export is not available on this server."  # shown to users; detail stays in logs

FONT_STACK = (
    '"Space Grotesk", "Segoe UI", "Helvetica Neue", Arial, "Liberation Sans", "DejaVu Sans", sans-serif'
)
MERMAID_CONFIG: dict[str, Any] = {
    "startOnLoad": False,
    "securityLevel": "strict",
    "theme": "base",
    "fontFamily": FONT_STACK,
    "flowchart": {"htmlLabels": True, "useMaxWidth": True, "curve": "basis"},
    "sequence": {"useMaxWidth": True, "wrap": False},
    "themeVariables": {
        "fontFamily": FONT_STACK,
        "fontSize": "13px",
        "primaryColor": "#FBFAF6",
        "primaryBorderColor": "#24506B",
        "primaryTextColor": "#14161C",
        "secondaryColor": "#F2E0BC",
        "tertiaryColor": "#F1EEE6",
        "lineColor": "#5B5E68",
        "clusterBkg": "#F1EEE6",
        "clusterBorder": "#C9C4B8",
        "actorBkg": "#FBFAF6",
        "actorBorder": "#24506B",
        "noteBkgColor": "#F2E0BC",
        "noteBorderColor": "#B87A22",
    },
}

# Runs via CDP evaluation (not a page script). Renders each diagram on its own so one failure is contained.
_RENDER_DIAGRAMS_JS = """
async ({config, perDiagramMs}) => {
  const els = Array.from(document.querySelectorAll('pre.mermaid'));
  if (els.length === 0) return {total: 0, rendered: 0, failed: 0, svgs: 0};
  if (typeof mermaid === 'undefined') throw new Error('mermaid bundle not loaded');
  mermaid.initialize(config);
  let rendered = 0, failed = 0;
  for (const el of els) {
    const source = el.textContent;
    try {
      let timer;
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('diagram timeout')), perDiagramMs);
      });
      try {
        await Promise.race([mermaid.run({nodes: [el], suppressErrors: false}), timeout]);
      } finally {
        clearTimeout(timer);
      }
      const svg = el.querySelector('svg');
      if (!svg || svg.getAttribute('aria-roledescription') === 'error') throw new Error('no diagram produced');
      rendered++;
    } catch (e) {
      failed++;
      el.removeAttribute('data-processed');
      el.classList.remove('mermaid');
      el.classList.add('mermaid-failed');
      el.textContent = source;
    }
  }
  document.querySelectorAll('body > [id^="dmermaid"], body > [id^="mermaid-"]').forEach((n) => n.remove());
  const svgs = document.querySelectorAll('pre.mermaid svg').length;
  if (document.fonts && document.fonts.ready) await document.fonts.ready;
  return {total: els.length, rendered, failed, svgs};
}
"""

_HEADER_TEMPLATE = "<div></div>"
_FOOTER_TEMPLATE = (
    '<div style="width:100%;font-size:7.5px;color:#8A8D96;padding:0 16mm;display:flex;'
    'justify-content:space-between;font-family:Arial,Helvetica,sans-serif;">'
    '<span>{label}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
)
_HEAD_OPEN = re.compile(r"<head[^>]*>", re.IGNORECASE)


@cache
def _mermaid_source() -> str:
    try:
        return MERMAID_JS.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExportError(PDF_UNAVAILABLE, retryable=False, detail={"missing": str(MERMAID_JS)}) from exc


def _csp(script: str) -> str:
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    return (
        "default-src 'none'; "
        f"script-src 'sha256-{digest}'; "
        "style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; "
        "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    )


def with_csp(html: str, script: str) -> str:
    meta = (
        f'<meta http-equiv="Content-Security-Policy" content="{html_lib.escape(_csp(script), quote=True)}">'
    )
    m = _HEAD_OPEN.search(html)
    if m is None:
        return f"<!doctype html><html><head>{meta}</head><body>{html}</body></html>"
    return html[: m.end()] + meta + html[m.end() :]


async def _block_network(route: Route) -> None:
    url = route.request.url
    if url.startswith(("data:", "about:blank")):
        await route.continue_()
    else:
        log.warning("export.network_blocked", url=url[:200])
        await route.abort("blockedbyclient")


_ENV_ALLOW = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "LANG",
    "LC_ALL",
    "DISPLAY",
    "FONTCONFIG_PATH",
    "XDG_RUNTIME_DIR",
    "PLAYWRIGHT_BROWSERS_PATH",
)


def _browser_env() -> dict[str, str | float | bool]:
    """Only what a headless browser needs to start: never the worker's secrets."""
    return {k: v for k in _ENV_ALLOW if (v := os.environ.get(k)) is not None}


@dataclass(frozen=True)
class PdfResult:
    pdf: bytes
    diagrams_total: int
    diagrams_rendered: int
    diagrams_failed: int
    svg_count: int
    page_html: str | None = None  # the DOM just before printing, only when requested (debugging/tests)


class PdfRenderer:
    """One Chromium per process, launched lazily on first use; each render gets a fresh isolated context."""

    def __init__(
        self,
        *,
        timeout_s: float = 90.0,
        diagram_timeout_s: float = 15.0,
        chromium_sandbox: bool = False,
    ) -> None:
        self._timeout_s = timeout_s
        self._diagram_timeout_s = diagram_timeout_s
        self._chromium_sandbox = chromium_sandbox
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            try:
                if self._pw is None:
                    self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=True,
                    env=_browser_env(),  # the worker's database, store and model credentials stay out of Chromium
                    chromium_sandbox=self._chromium_sandbox,
                    args=["--disable-dev-shm-usage", "--disable-gpu", "--no-first-run"],
                )
            except PlaywrightError as exc:
                log.error("export.chromium_launch_failed", error=str(exc)[:500])
                raise ExportError(PDF_UNAVAILABLE, retryable=False, detail={"error": str(exc)[:500]}) from exc
            return self._browser

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except PlaywrightError:
                    log.warning("export.browser_close_failed")
                self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    async def render(self, html: str, *, footer_label: str = "Unfold") -> bytes:
        return (await self.render_with_report(html, footer_label=footer_label)).pdf

    async def render_with_report(
        self, html: str, *, footer_label: str = "Unfold", capture_html: bool = False
    ) -> PdfResult:
        with span("export.pdf_render", html_bytes=len(html)):
            try:
                return await asyncio.wait_for(self._render(html, footer_label, capture_html), self._timeout_s)
            except TimeoutError as exc:
                raise ExportError(f"PDF rendering timed out after {self._timeout_s:.0f}s") from exc
            except PlaywrightTimeoutError as exc:
                raise ExportError("PDF rendering timed out", detail={"error": str(exc)[:500]}) from exc
            except PlaywrightError as exc:
                raise ExportError("PDF rendering failed", detail={"error": str(exc)[:500]}) from exc

    async def _render(self, html: str, footer_label: str, capture_html: bool) -> PdfResult:
        script = _mermaid_source()
        browser = await self._ensure_browser()
        context = await browser.new_context(offline=True, java_script_enabled=True, service_workers="block")
        try:
            page = await context.new_page()
            page.set_default_timeout(self._timeout_s * 1000)
            await page.route("**/*", _block_network)
            needs_mermaid = 'class="mermaid"' in html
            await page.set_content(with_csp(html, script), wait_until="load")
            stats: dict[str, int] = {"total": 0, "rendered": 0, "failed": 0, "svgs": 0}
            if needs_mermaid:
                await page.add_script_tag(content=script)
                stats = await page.evaluate(
                    _RENDER_DIAGRAMS_JS,
                    {"config": MERMAID_CONFIG, "perDiagramMs": int(self._diagram_timeout_s * 1000)},
                )
            if stats["failed"]:
                log.warning("export.diagrams_failed", failed=stats["failed"], total=stats["total"])
            page_html = await page.content() if capture_html else None
            pdf = await page.pdf(
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template=_HEADER_TEMPLATE,
                footer_template=_FOOTER_TEMPLATE.format(
                    label=html_lib.escape(footer_label[:120], quote=True)
                ),
                margin={"top": "16mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
            )
        finally:
            await context.close()
        log.info("export.pdf_rendered", bytes=len(pdf), diagrams=stats["total"], failed=stats["failed"])
        return PdfResult(
            pdf=pdf,
            diagrams_total=stats["total"],
            diagrams_rendered=stats["rendered"],
            diagrams_failed=stats["failed"],
            svg_count=stats["svgs"],
            page_html=page_html,
        )
