#!/usr/bin/env python3
"""Pre-deploy web gate -- run the a11y + Lighthouse checks against what circulation
actually serves.

Both gates existed before this and both pointed at `scratch/chrome-mockups/*.html`,
so they graded the design mockups rather than the Rust templates that ship. This
brings circulation up on a local port, discovers the real route set (including the
latest issue and a thread detail, which only exist at runtime), and runs:

  1. bin/a11y-check --url ...   structural invariants, seconds, always
  2. bin/lighthouse --url ...   a11y/best-practices/SEO scores, ~15s/page, opt-out

Usage:
    bin/web-check                     # compose up, check, tear down
    bin/web-check --fast              # skip Lighthouse (a11y invariants only)
    bin/web-check --base http://localhost:8081   # check an already-running server
    bin/web-check --min 95
"""

import argparse
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVICE = "digest-circulation"
# 127.0.0.1, not localhost: localhost resolves to ::1 first and the published
# port does not answer there, so the gate would time out against a healthy server.
DEFAULT_BASE = "http://127.0.0.1:8080"

# Every server-rendered HTML page. Non-HTML routes (/feed.xml, /og-image.png,
# /robots.txt, /health) have nothing for either gate to assert.
STATIC_ROUTES = ["/", "/sources", "/stats", "/threads", "/search", "/feedback", "/privacy", "/connect"]

# Measured 2026-07-28: 17 kB (/feedback) to 149 kB (/threads), which lists every
# thread and so grows with content. 200 kB is a creep detector with headroom, not a
# performance target -- when /threads reaches it, the answer is pagination, not a
# bigger number.
DEFAULT_MAX_KB = 200

THREAD_LINK = re.compile(r'href=["\']/thread/([A-Za-z0-9_-]+)["\']')


def http_fetch(base):
    """Returns fetch(path) -> (status, location, body); redirects are NOT followed."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def fetch(path):
        try:
            with opener.open(base + path, timeout=20) as r:
                return r.status, r.headers.get("Location"), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location"), ""

    return fetch


def local_path(base, location):
    """The path part of a redirect target, or None if it points off this server.
    Lighthouse and urllib both follow redirects, so an off-base route would be
    graded against somebody else's site -- /privacy 307s to seanfloyd.dev and
    scored its noindex as our SEO failure until this dropped it."""
    if not location:
        return None
    if location.startswith("/"):
        return location
    origin = re.match(r"^https?://[^/]+", location)
    if origin and origin.group(0) == base.rstrip("/"):
        return location[origin.end() :] or "/"
    return None


def discover_routes(base, fetch):
    """Returns (urls, warnings, failures).

    The split is the whole point. A *warning* is a page that legitimately has
    nothing to grade: /privacy redirects off-site, the database has no issue yet.
    A *failure* is a page that should have served and did not, which is precisely
    what a pre-deploy gate exists to catch. Lumping the second into the first let
    the gate print 'passed' while shipping a 500.
    """
    urls = []
    warnings = []
    failures = []

    for route in STATIC_ROUTES:
        try:
            status, location, _ = fetch(route)
        except Exception as e:
            failures.append(f"{route}: fetch failed: {e}")
            continue
        if status in (301, 302, 307, 308):
            path = local_path(base, location)
            if path is None:
                warnings.append(f"{route}: redirects off-site to {location} -- not ours to grade")
                continue
            urls.append(base + path)
        elif status == 200:
            urls.append(base + route)
        else:
            failures.append(f"{route}: HTTP {status}")

    try:
        status, location, _ = fetch("/today")
    except Exception as e:
        failures.append(f"could not resolve the latest issue (/today): {e}")
    else:
        # /today redirects to an absolute canonical URL when DIGEST_DOMAIN is set; reduce
        # it to this server so the gate grades the build under test, not production.
        path = re.sub(r"^https?://[^/]+", "", location) if location else None
        if path:
            urls.append(base + path)
        elif status == 404:
            # handlers::today 404s only when `digests` is genuinely empty -- a normal
            # state on a fresh box, and not something to block a deploy over.
            warnings.append("no issue to check -- database has no digests yet")
        else:
            failures.append(f"/today returned {status} (a 5xx here means the database is unreadable)")

    try:
        status, _, body = fetch("/threads")
    except Exception as e:
        failures.append(f"could not resolve a thread detail (/threads): {e}")
    else:
        match = THREAD_LINK.search(body)
        if match:
            urls.append(f"{base}/thread/{match.group(1)}")
        elif status == 200:
            warnings.append("no thread detail to check -- /threads listed none")
        # a non-200 /threads was already recorded as a failure by the static loop

    return urls, warnings, failures


def wait_for_health(base, timeout=90):
    """Returns (healthy, last_error). Keeping the last error matters: /health 503s
    with the missing tables named in its body when a migration has not run, and
    swallowing that reports a 90-second 'never came up' with the cause discarded."""
    deadline = time.time() + timeout
    last_error = "no response"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=5) as r:
                if r.status == 200:
                    return True, None
                last_error = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
        except Exception as e:
            last_error = str(e)
        time.sleep(1)
    return False, last_error


def compose(*args):
    return subprocess.run(["docker", "compose", *args], cwd=REPO, check=False)


def run_gate(name, cmd):
    print(f"\n=== {name} ===")
    return subprocess.run(cmd, cwd=REPO, check=False).returncode == 0


def check_page_weight(urls, max_kb):
    """Cap the served HTML per page.

    Every page inlines the full chrome CSS, the design tokens and its own CSS into
    one critical <style> (assets::head_style), which is the right call for render
    performance but means page weight creeps with nothing watching it. Lighthouse
    would only see this under the performance category, which this gate does not
    run; measuring the served bytes directly costs one request we already make.
    """
    print(f"\n=== Page weight (max {max_kb} kB) ===")
    ok = True
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                kb = len(r.read()) / 1024
        except Exception as e:
            print(f"  [FAIL] {url}  could not measure: {e}")
            ok = False
            continue
        over = kb > max_kb
        if over:
            ok = False
        print(f"  [{'FAIL' if over else ' ok '}] {url:52} {kb:6.1f} kB")
    if not ok:
        print("\nPage weight gate FAILED.")
    return ok


def url_args(urls):
    """['http://a', 'http://b'] -> ['--url', 'http://a', '--url', 'http://b']."""
    return [arg for url in urls for arg in ("--url", url)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="check an already-running server instead of starting one")
    ap.add_argument("--fast", action="store_true", help="skip Lighthouse (structural a11y only)")
    ap.add_argument("--min", type=int, default=100, help="minimum Lighthouse score (default 100)")
    ap.add_argument(
        "--max-kb", type=int, default=DEFAULT_MAX_KB, help=f"max served HTML per page (default {DEFAULT_MAX_KB})"
    )
    args = ap.parse_args()

    # Subprocess output goes straight to the terminal; without this our own prints
    # buffer and the report reads out of order.
    sys.stdout.reconfigure(line_buffering=True)

    base = (args.base or DEFAULT_BASE).rstrip("/")
    started = False

    if not args.base:
        # Reuse an already-running dev server rather than rebuilding it and then
        # stopping it out from under whoever started it.
        already_running, _ = wait_for_health(base, timeout=1)
        if already_running:
            print(f"Reusing the {SERVICE} already on {base}")
        else:
            print(f"Starting {SERVICE} ...")
            if compose("up", "-d", "--build", SERVICE).returncode != 0:
                sys.exit("error: could not start circulation")
            started = True

    try:
        healthy, last_error = wait_for_health(base)
        if not healthy:
            sys.exit(f"error: {base}/health never came up ({last_error})")

        urls, warnings, failures = discover_routes(base, http_fetch(base))
        for w in warnings:
            print(f"  [warn] {w}")
        for f in failures:
            print(f"  [FAIL] {f}")

        if failures:
            sys.exit(f"\nWeb gate FAILED: {len(failures)} route(s) did not serve.")
        # Never hand an empty --url list to the sub-gates: with no targets they fall
        # back to globbing the design mockups and exit 0, so the gate would report
        # success having verified nothing. That failed OPEN on a box where scratch/
        # exists and closed on a fresh clone, which is the worst of both.
        if not urls:
            sys.exit(f"\nWeb gate FAILED: resolved 0 checkable pages on {base}.")

        print(f"\nChecking {len(urls)} pages on {base}")
        ok = run_gate("a11y invariants", [sys.executable, "bin/a11y-check", *url_args(urls)])
        ok &= check_page_weight(urls, args.max_kb)
        if args.fast:
            print("\n=== Lighthouse === skipped (--fast)")
        else:
            ok &= run_gate("Lighthouse", [sys.executable, "bin/lighthouse", "--min", str(args.min), *url_args(urls)])
    finally:
        if started:
            rc = compose("stop", SERVICE).returncode
            if rc != 0:
                print(f"  [warn] `docker compose stop {SERVICE}` exited {rc}; it may still hold the port")

    if warnings:
        print(f"\n{len(warnings)} route(s) had nothing to check (see [warn] above).")
    if not ok:
        sys.exit("\nWeb gate FAILED.")
    print("\nWeb gate passed.")


if __name__ == "__main__":
    main()
