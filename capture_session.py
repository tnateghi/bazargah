"""
Open a real Chromium window, let YOU log in and do ONE full cycle manually,
while we record every network request/response (HAR + live POST log).
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from browser_helpers import CHROMIUM_ARGS, LAUNCH_VIEWPORT_KWARGS, prepare_browser_page

TARGET_URL = "https://sps.bki.ir/Pages/AddZirMajmooeKhodEzhari.aspx"
OUT_DIR = Path(__file__).parent / "captures"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    events_path = OUT_DIR / f"requests_{stamp}.jsonl"
    har_path = OUT_DIR / f"session_{stamp}.har"
    summary_path = OUT_DIR / f"summary_{stamp}.json"

    events: list[dict] = []
    post_count = 0

    print("=" * 60)
    print("مرورگر باز می‌شود.")
    print("1) لاگین کن (حتما داخل همین پنجره)")
    print("2) برو به صفحه افزودن زیرمجموعه")
    print("3) یک سیکل کامل انجام بده:")
    print("   سرچ شناسه → تعلیق → سرچ مجدد → پر کردن فیلدها → ذخیره")
    print("4) وقتی تمام شد، در همین ترمینال Enter بزن")
    print("هر POST که بزند اینجا چاپ می‌شود تا مطمئن شوی ضبط کار می‌کند.")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=50,
            args=CHROMIUM_ARGS,
        )
        context = browser.new_context(
            ignore_https_errors=True,
            record_har_path=str(har_path),
            record_har_content="embed",
            **LAUNCH_VIEWPORT_KWARGS,
        )
        page = context.new_page()
        prepare_browser_page(page)

        def on_request(req):
            nonlocal post_count
            try:
                post = None
                if req.method.upper() in {"POST", "PUT", "PATCH"}:
                    post = req.post_data
                    post_count += 1
                    print(f"\n[POST #{post_count}] {req.url}")
                    if post:
                        preview = post[:300].replace("\n", " ")
                        print(f"  body: {preview}...")
                events.append(
                    {
                        "t": time.time(),
                        "kind": "request",
                        "method": req.method,
                        "url": req.url,
                        "resource_type": req.resource_type,
                        "headers": dict(req.headers),
                        "post_data": post,
                    }
                )
            except Exception as e:
                events.append({"kind": "request_error", "error": str(e)})

        def on_response(res):
            try:
                body = None
                ctype = (res.headers.get("content-type") or "").lower()
                if "text" in ctype or "json" in ctype or "html" in ctype:
                    try:
                        txt = res.text()
                        if txt and len(txt) < 400_000:
                            body = txt
                    except Exception:
                        body = None
                events.append(
                    {
                        "t": time.time(),
                        "kind": "response",
                        "status": res.status,
                        "url": res.url,
                        "headers": dict(res.headers),
                        "body": body,
                    }
                )
            except Exception as e:
                events.append({"kind": "response_error", "error": str(e)})

        page.on("request", on_request)
        page.on("response", on_response)
        page.goto(TARGET_URL, wait_until="domcontentloaded")

        input("\n>>> بعد از انجام یک سیکل کامل، اینجا Enter بزن... ")

        with events_path.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

        posts = [
            e
            for e in events
            if e.get("kind") == "request" and e.get("method", "").upper() == "POST"
        ]
        summary = {
            "saved_at": stamp,
            "total_events": len(events),
            "post_count": len(posts),
            "post_urls": sorted({e["url"] for e in posts}),
            "events_file": str(events_path),
            "har_file": str(har_path),
            "final_url": page.url,
        }
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        context.close()
        browser.close()

    print("\nذخیره شد:")
    print(" ", events_path)
    print(" ", har_path)
    print(" ", summary_path)
    print(f"تعداد POSTها: {len(posts)}")
    if not posts:
        print("\n⚠️ هیچ POSTی ضبط نشد. یعنی لاگین/تعلیق/ذخیره داخل همین پنجره انجام نشده.")
    for u in summary["post_urls"]:
        print("  POST ->", u)


if __name__ == "__main__":
    main()
