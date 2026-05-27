#!/usr/bin/env python3
"""
Update the NexusMods short description and full description for Bethesda Strings Editor.

Usage:
    python scripts/update_nexusmods_page.py

Opens a Chromium window.  If you are not already logged in to NexusMods,
log in manually in the browser — the script waits up to 2 minutes.
Cookies are saved after the first run so subsequent runs skip the login step.
"""

import asyncio
import json
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("Install playwright first:  pip install playwright && python -m playwright install chromium")

# ── Constants ─────────────────────────────────────────────────────────────────

REPO_ROOT    = Path(__file__).parent.parent
MOD_EDIT_URL = "https://www.nexusmods.com/starfield/mods/17158/edit"
COOKIES_PATH = Path.home() / ".config" / "BethesdaModTools" / "nexusmods_cookies.json"
HTML_PATH    = REPO_ROOT / "resources" / "nexusmods_description.html"

SHORT_DESC = (
    "AI-powered localization editor for Starfield. Translates .strings, .dlstrings, "
    ".ilstrings, BA2 archives, and ESP/ESM plugins across 11 languages via a local "
    "Ollama model. Includes translation memory, term protection, quality checker, "
    "and xTranslator XML support."
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_cookies() -> list:
    if COOKIES_PATH.exists():
        return json.loads(COOKIES_PATH.read_text())
    return []


def _save_cookies(cookies: list) -> None:
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(json.dumps(cookies, indent=2))
    COOKIES_PATH.chmod(0o600)
    print(f"  Cookies saved → {COOKIES_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def run() -> None:
    full_html = HTML_PATH.read_text(encoding="utf-8")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, slow_mo=30)
        ctx     = await browser.new_context(viewport={"width": 1400, "height": 900})

        saved = _load_cookies()
        if saved:
            await ctx.add_cookies(saved)
            print("Loaded saved cookies.")

        page = await ctx.new_page()

        # ── Navigate ──────────────────────────────────────────────────────────
        print(f"Opening {MOD_EDIT_URL} …")
        await page.goto(MOD_EDIT_URL, timeout=60_000)

        # Wait for login if redirected
        if "/login" in page.url or "signin" in page.url.lower():
            print("\nPlease log in to NexusMods in the browser window.")
            print("Waiting up to 2 minutes…\n")
            try:
                await page.wait_for_url(
                    lambda u: "edit" in u, timeout=120_000
                )
            except PWTimeout:
                sys.exit("Timed out waiting for login. Re-run the script and log in faster.")

        await page.wait_for_load_state("networkidle", timeout=30_000)
        print("Edit page loaded.")

        # ── Short description (plain textarea) ────────────────────────────────
        print("Updating short description…")
        short_field = page.locator("textarea").first
        await short_field.click(timeout=10_000)
        await page.keyboard.press("Control+a")
        await short_field.fill(SHORT_DESC)
        print(f"  → {SHORT_DESC[:60]}…")

        # ── Full description (TinyMCE editor) ─────────────────────────────────
        print("Updating full description via TinyMCE API…")
        escaped = json.dumps(full_html)   # safely escape for JS string literal

        result = await page.evaluate(f"""() => {{
            // TinyMCE exposes a global `tinymce` object.
            // Editors are indexed 0-based; the description is usually index 0 or 1.
            if (typeof tinymce === 'undefined') return 'TinyMCE not found';

            // Find the editor whose target element is inside the "full description" block.
            // Fallback: use the last editor if we can't identify by label.
            let editor = null;
            for (const ed of tinymce.editors) {{
                const label = ed.getElement()?.closest('section,div')
                                ?.querySelector('label,h2,h3')?.textContent || '';
                if (/full|descri/i.test(label)) {{ editor = ed; break; }}
            }}
            if (!editor) editor = tinymce.editors[tinymce.editors.length - 1];
            if (!editor) return 'No TinyMCE editor found';

            editor.setContent({escaped});
            editor.fire('change');
            editor.fire('input');
            return 'ok:' + editor.id;
        }}""")

        if not result.startswith("ok"):
            print(f"  TinyMCE API returned: {result}")
            print("  Falling back to source-button method…")
            await _source_button_fallback(page, full_html)
        else:
            print(f"  TinyMCE editor updated ({result})")

        # ── Save ──────────────────────────────────────────────────────────────
        print("Clicking Save…")
        save_btn = page.locator('button:has-text("Save"), input[value="Save"]').first
        await save_btn.click(timeout=10_000)
        await page.wait_for_load_state("networkidle", timeout=30_000)
        print("Saved.")

        # Persist cookies
        _save_cookies(await ctx.cookies())

        print("\nDone! Verify at: https://www.nexusmods.com/starfield/mods/17158")
        await page.wait_for_timeout(4_000)
        await browser.close()


async def _source_button_fallback(page, html: str) -> None:
    """Click the [] (source) toolbar button and paste raw HTML into the textarea."""
    # The [] button title in TinyMCE is typically "Source code"
    source_btn = page.locator(
        'button[title*="Source"], button[aria-label*="Source"], '
        'button[title*="source"], button[data-mce-name="code"]'
    ).first
    await source_btn.click(timeout=8_000)
    await page.wait_for_timeout(500)

    # A dialog textarea appears
    dialog_ta = page.locator('div[role="dialog"] textarea').first
    await dialog_ta.wait_for(timeout=8_000)
    await dialog_ta.click()
    await page.keyboard.press("Control+a")

    # type() is slow for large content — use fill() instead
    await dialog_ta.fill(html)

    # Confirm the dialog
    ok_btn = page.locator('div[role="dialog"] button:has-text("OK"), div[role="dialog"] button:has-text("Save")').first
    await ok_btn.click(timeout=8_000)
    print("  Source dialog method succeeded.")


if __name__ == "__main__":
    asyncio.run(run())
