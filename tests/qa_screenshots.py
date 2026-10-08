"""Regenerate the README screenshots and the site's social preview card.

    python tests/qa_screenshots.py

Needs the built pages (``python -m sf_flat_routes map``), Playwright and a
Chromium (``playwright install chromium``). Writes
``outputs/screenshot_{route_finder,interactive,warped}.png`` and
``site/preview.jpg`` (1200x630, the size the og:image tags declare).
"""
import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sf_flat_routes.config import OUTPUT_DIR, SITE_DIR  # noqa: E402
from sf_flat_routes.viz_interactive import INTERACTIVE_HTML, SIMPLE_HTML  # noqa: E402

READY = ("window.App && App.family && !App.family.partial"
         " && !document.getElementById('result').hidden")


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])

        # the route finder on its opening trip, slider in the middle so the
        # fan of routes either side of the shown one is visible
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(SIMPLE_HTML.resolve().as_uri(), wait_until="load", timeout=240_000)
        page.wait_for_function(READY, timeout=240_000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUTPUT_DIR / "screenshot_route_finder.png"))

        card = browser.new_page(viewport={"width": 1200, "height": 630})
        card.goto(SIMPLE_HTML.resolve().as_uri(), wait_until="load", timeout=240_000)
        card.wait_for_function(READY, timeout=240_000)
        card.wait_for_timeout(2500)
        jpg = Image.open(io.BytesIO(card.screenshot())).convert("RGB")
        jpg.save(SITE_DIR / "preview.jpg", "JPEG", quality=86, optimize=True)

        # the explorer on its first guided example, then the warped city
        ex = browser.new_page(viewport={"width": 1440, "height": 900})
        ex.goto(INTERACTIVE_HTML.resolve().as_uri(), wait_until="load", timeout=240_000)
        ex.wait_for_function("window.App && window.App.graph", timeout=240_000)
        ex.wait_for_timeout(3000)
        ex.screenshot(path=str(OUTPUT_DIR / "screenshot_interactive.png"))
        ex.click("#warptoggle")
        ex.wait_for_function(
            "App.warp && !document.getElementById('busy').classList.contains('on')",
            timeout=180_000)
        ex.wait_for_timeout(2500)
        ex.screenshot(path=str(OUTPUT_DIR / "screenshot_warped.png"))
        browser.close()
    for name in ("screenshot_route_finder.png", "screenshot_interactive.png",
                 "screenshot_warped.png"):
        print(OUTPUT_DIR / name)
    print(SITE_DIR / "preview.jpg")


if __name__ == "__main__":
    main()
