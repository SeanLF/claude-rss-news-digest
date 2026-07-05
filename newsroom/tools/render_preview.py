"""Fast local render + screenshot preview for the digest template/CSS.

No Docker: renders a selections fixture through the REAL template + tokens.css +
digest.css and emits the true artifacts a reader gets --
  - EMAIL: prepare_for_email (vars -> light, premailer-inlined, web-only stripped)
  - WEB:   prepare_for_web + the .email-only/.web-only flip, light and dark
-- then screenshots each at desktop and mobile widths with headless Chrome.

    make preview                          # kitchen-sink fixture, every visual state
    make preview FIXTURE=path/to.json
    bin/render-preview [fixture.json]

Output: scratch/preview/ (gitignored). Chrome path via $CHROME (defaults to macOS
Google Chrome); screenshots are skipped with a note if Chrome isn't found.
Pillow (optional, in the venv) trims trailing whitespace from tall shots.

NOTE: the web flip below mirrors circulation's DIGEST_NAV_CSS for preview fidelity
only. The authoritative email<->web contract is enforced by
newsroom/tests/test_pipeline_contract.py, not here.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "newsroom" / "src"))

import config
import db
import render

REPO = Path(__file__).resolve().parents[2]
config.TOKENS_FILE = REPO / "design" / "tokens.css"
STYLES = REPO / "newsroom" / "templates" / "digest.css"
TEMPLATE = REPO / "newsroom" / "templates" / "digest-template.html"

CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
OUT = REPO / "scratch" / "preview"
OUT.mkdir(parents=True, exist_ok=True)
DEFAULT_FIXTURE = REPO / "newsroom" / "tests" / "fixtures" / "kitchensink_selections.json"

# Mirrors circulation's DIGEST_NAV_CSS visibility flip (preview fidelity only).
WEB_FLIP = "<style>.email-only{display:none}.web-only{display:block}footer nav .web-only{display:inline}</style>"


def main() -> None:
    fixture_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIXTURE
    fixture = json.loads(fixture_path.read_text())

    os.environ.setdefault("DIGEST_DOMAIN", "digest.seanfloyd.dev")
    os.environ.setdefault("ARCHIVE_URL", "https://digest.seanfloyd.dev")
    os.environ.setdefault("AUTHOR_NAME", "Sean Floyd")
    os.environ.setdefault("AUTHOR_URL", "https://seanfloyd.dev")

    try:
        db.init(REPO / "data" / "digest-cloud.db", REPO / "migrations", apply_migrations=False)
    except Exception as e:  # issue number is best-effort in a preview
        print("db.init skipped (issue number blank):", e)

    raw_path = OUT / "_raw.html"
    raw_path.write_text(render.render_digest(fixture, TEMPLATE))
    render.replace_placeholders(raw_path, fixture, STYLES, preheader=fixture.get("preheader", ""))
    raw = raw_path.read_text()

    web_base = db.prepare_for_web(raw)

    def web_variant(theme: str) -> str:
        return web_base.replace("</head>", WEB_FLIP + "</head>").replace(
            '<html lang="en">', f'<html lang="en" data-theme="{theme}">'
        )

    variants = {
        "email": render.prepare_for_email(raw),
        "web-light": web_variant("light"),
        "web-dark": web_variant("dark"),
    }

    chrome_ok = Path(CHROME).exists()
    if not chrome_ok:
        print(f"Chrome not found at {CHROME!r}; writing HTML only (set $CHROME to enable screenshots).")

    for name, html in variants.items():
        (OUT / f"{name}.html").write_text(html)
        if chrome_ok:
            for width in (720, 380):
                _shoot(name, width)
    print(f"\nDONE -> {OUT.relative_to(REPO)}/  (fixture: {fixture_path.name})")


def _shoot(name: str, width: int) -> None:
    src = OUT / f"{name}.html"
    out = OUT / f"{name}--{width}w.png"
    subprocess.run(
        [
            CHROME,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-color-profile=srgb",
            f"--window-size={width},4200",
            f"--screenshot={out}",
            f"file://{src}",
        ],
        check=True,
        capture_output=True,
    )
    # Headless Chrome can exit 0 having written a blank/truncated PNG (blocked
    # file:// asset, render not settled). These shots are the pre-send QA gate, so
    # warn rather than let a broken shot read as success.
    size = out.stat().st_size if out.exists() else 0
    if size < 3000:
        print(f"WARNING: {out.name} is only {size} bytes -- likely a blank/broken render")
    _trim(out)
    print("wrote", out.relative_to(REPO))


def _trim(png: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        return  # trimming is cosmetic; `pip install pillow` in the venv enables it
    im = Image.open(png).convert("RGB")
    w, h = im.size
    px = im.load()
    bg = px[2, 2]
    last = h - 1
    while last > 0 and all(px[x, last] == bg for x in range(0, w, 7)):
        last -= 1
    im.crop((0, 0, w, min(h, last + 24))).save(png)


if __name__ == "__main__":
    main()
