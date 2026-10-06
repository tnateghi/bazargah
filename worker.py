"""
Browser worker with credential autofill.
Captcha is still manual; username/password come from data/credentials.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from browser_helpers import CHROMIUM_ARGS, LAUNCH_VIEWPORT_KWARGS, prepare_browser_page
from db import get_records_by_shenase, list_records, set_status

BASE = Path(__file__).parent
PROFILE_DIR = BASE / "data" / "browser_profile"
CREDS_FILE = BASE / "data" / "credentials.json"
LOG_FILE = BASE / "data" / "runs" / "worker.log"
TARGET_URL = "https://sps.bki.ir/Pages/AddZirMajmooeKhodEzhari.aspx"
ACTIVITY_VALUE = "1114"
MAJMOE_VALUE = "1"

DELAY_AFTER_SUSPEND = 7.0
DELAY_BETWEEN_RECORDS = 6.0
DELAY_AFTER_MAJMOE = 4.0
DELAY_AFTER_ACTIVITY = 2.0
DELAY_BEFORE_INSERT = 3.5

ID = {
    "text_part": "#ctl00_ContentPlaceHolder1_TextpartId",
    "btn_search": "#ctl00_ContentPlaceHolder1_BtnSearch",
    "ddl_majmoe": "#ctl00_ContentPlaceHolder1_ddlMajmoe",
    "txt_part_code": "#ctl00_ContentPlaceHolder1_txtPartidCode",
    "estelam": "#ctl00_ContentPlaceHolder1_Estelam",
    "ddl_faaliyat": "#ctl00_ContentPlaceHolder1_DDLFaaliyat",
    "dam_light": "#ctl00_ContentPlaceHolder1_DamLightTxt",
    "dam_heavy": "#ctl00_ContentPlaceHolder1_DamHeavyTxt",
    "damdar_name": "#ctl00_ContentPlaceHolder1_DamdarNametxt",
    "insert_btn": "#ctl00_ContentPlaceHolder1_InsertZirMajmooeBtn",
    "user": "#ctl00_ContentPlaceHolder1_UserName",
    "pass": "#ctl00_ContentPlaceHolder1_Password",
}


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def _setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fp = LOG_FILE.open("a", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, fp)
    sys.stderr = _Tee(sys.__stderr__, fp)


def load_credentials() -> dict:
    if not CREDS_FILE.exists():
        return {}
    try:
        return json.loads(CREDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def pause(sec: float, label: str = "") -> None:
    if label:
        print(f"  … صبر {sec:.1f}s {label}")
    time.sleep(sec)


def wait_idle(page, ms: int = 1000) -> None:
    page.wait_for_timeout(ms)


def check_activity_window(page) -> None:
    """Warn/fail if site shows blocked activity hours."""
    banner = page.locator("#ctl00_ContentPlaceHolder1_DivActivityTime")
    if banner.count() == 0 or not banner.first.is_visible():
        return
    text = banner.inner_text()
    start = page.locator("#ctl00_ContentPlaceHolder1_lblStartActivityTime")
    end = page.locator("#ctl00_ContentPlaceHolder1_lblEndActivityTime")
    start_t = start.inner_text().strip() if start.count() else "?"
    end_t = end.inner_text().strip() if end.count() else "?"
    # message means: NOT allowed between start and end
    now = datetime.now().strftime("%H:%M:%S")
    print(f"  اخطار سایت: محدودیت ساعتی {start_t} تا {end_t} (الان {now})")
    if "مقدور نمی‌باشد" in text:
        try:
            sh, sm, ss = [int(x) for x in start_t.split(":")[:3]]
            eh, em, es = [int(x) for x in end_t.split(":")[:3]]
            n = datetime.now().time()
            from datetime import time as dtime

            st = dtime(sh, sm, ss)
            et = dtime(eh, em, es)
            if st <= n <= et:
                raise RuntimeError(
                    f"الان در بازه ممنوع سایت هستید ({start_t} تا {end_t}). "
                    "بعداً دوباره تلاش کنید."
                )
        except RuntimeError:
            raise
        except Exception:
            pass


def ensure_logged_in(page, timeout_sec: int = 600) -> None:
    page.goto(TARGET_URL, wait_until="domcontentloaded")
    wait_idle(page, 1200)

    if "Login.aspx" not in page.url:
        return

    creds = load_credentials()
    user = (creds.get("username") or "").strip()
    password = (creds.get("password") or "").strip()

    print("=" * 60)
    print("صفحه لاگین")
    if user and password:
        try:
            page.fill(ID["user"], user)
            page.fill(ID["pass"], password)
            print("یوزر/پسورد از تنظیمات پر شد.")
            print("فقط کپچا را وارد کن و دکمه ورود را بزن.")
        except Exception as e:
            print(f"پر کردن خودکار ناموفق: {e}")
            print("لاگین را دستی انجام بده.")
    else:
        print("یوزر/پسورد ذخیره نشده. کامل دستی لاگین کن.")
        print("(در پنل می‌توانی یوزر/پسورد را ذخیره کنی)")
    print("بعد از ورود موفق، ربات خودکار ادامه می‌دهد.")
    print("=" * 60)

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if "Login.aspx" not in page.url:
            print("لاگین تشخیص داده شد.")
            break
        page.wait_for_timeout(1500)
    else:
        raise RuntimeError("زمان لاگین تمام شد")

    page.goto(TARGET_URL, wait_until="domcontentloaded")
    wait_idle(page, 1500)
    if "Login.aspx" in page.url:
        raise RuntimeError("نشست لاگین برقرار نشد")


def fill(page, selector: str, value: str) -> None:
    page.wait_for_selector(selector, timeout=20000)
    loc = page.locator(selector)
    # if disabled, wait a bit for enable
    for _ in range(20):
        if loc.is_enabled():
            break
        page.wait_for_timeout(250)
    loc.fill("")
    loc.fill(str(value))


def click(page, selector: str) -> None:
    page.wait_for_selector(selector, timeout=20000)
    page.locator(selector).click()
    wait_idle(page, 1200)


def select_value(page, selector: str, value: str) -> None:
    page.wait_for_selector(selector, timeout=20000)
    page.select_option(selector, value=str(value))
    wait_idle(page, 1200)


def page_alert_text(page) -> str:
    """Collect visible validation / error messages from page."""
    texts = []
    for sel in [
        ".callout",
        ".alert",
        "#ctl00_ContentPlaceHolder1_DivActivityTime",
        "[id*='lbl']",
        ".field-validation-error",
        ".text-danger",
    ]:
        try:
            locs = page.locator(sel)
            n = min(locs.count(), 8)
            for i in range(n):
                t = locs.nth(i).inner_text().strip()
                if t and len(t) < 300:
                    texts.append(t)
        except Exception:
            pass
    # dialogs already handled separately
    blob = " | ".join(texts)
    return blob


# Success = green message on page. Errors we already know from site.
SUCCESS_PHRASES = (
    "با موفقیت",
    "موفقیت آمیز",
    "موفقیت‌آمیز",
    "افزوده شد",
    "ثبت شد",
    "انجام شد",
    "ذخیره شد",
)
ERROR_PHRASES = (
    "امکان افزودن مجدد وجود ندارد",
    "لطفا نوع زیر مجموعه را وارد",
    "نوع زیر مجموعه را وارد",
    "خطا",
    "ناموفق",
    "مقدور نمی‌باشد",
)


def _collect_colored_messages(page) -> dict:
    """Find short visible texts colored green (success) or red (error)."""
    return page.evaluate(
        """() => {
          const out = {green: [], red: [], any: []};
          const seen = new Set();
          const nodes = document.querySelectorAll(
            'span, label, div, p, td, font, strong, b, h1, h2, h3, h4, li'
          );
          for (const el of nodes) {
            if (!el || el.offsetParent === null) continue;
            const raw = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
            if (!raw || raw.length < 3 || raw.length > 220) continue;
            // Prefer leaf-ish nodes so we don't grab huge containers
            if (el.children && el.children.length > 4) continue;
            const style = window.getComputedStyle(el);
            const m = (style.color || '').match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/i);
            if (!m) continue;
            const r = +m[1], g = +m[2], b = +m[3];
            const key = raw;
            if (seen.has(key)) continue;
            const isGreen = g >= 110 && g > r + 25 && g > b + 25;
            const isRed = r >= 140 && r > g + 40 && r > b + 40;
            if (!isGreen && !isRed) continue;
            seen.add(key);
            const item = {text: raw, color: `rgb(${r},${g},${b})`};
            out.any.push(item);
            if (isGreen) out.green.push(item);
            if (isRed) out.red.push(item);
          }
          return out;
        }"""
    )


def confirm_insert_success(page, timeout_sec: float = 10.0) -> str:
    """
    Wait for the site's green success message after Insert.
    Returns the success text. Raises RuntimeError on error/no green message.
    """
    deadline = time.time() + timeout_sec
    last_hint = ""
    while time.time() < deadline:
        body = ""
        try:
            body = page.content()
        except Exception:
            pass
        for err in ERROR_PHRASES:
            if err in body and err not in ("خطا",):  # 'خطا' alone too broad in full HTML
                # still check colored red below; only hard-fail known business errors here
                if err in (
                    "امکان افزودن مجدد وجود ندارد",
                    "لطفا نوع زیر مجموعه را وارد",
                    "نوع زیر مجموعه را وارد",
                ):
                    raise RuntimeError(f"سایت: {err}")

        colored = {"green": [], "red": [], "any": []}
        try:
            colored = _collect_colored_messages(page)
        except Exception:
            pass

        for item in colored.get("green") or []:
            text = (item.get("text") or "").strip()
            if not text or text in {"14:00:00", "15:00:00"}:
                continue
            if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text):
                continue
            if "گزارش" in text and "زیرمجموعه" in text:
                continue
            if any(p in text for p in SUCCESS_PHRASES):
                return text
            # پیام سبز کوتاه سایت معمولاً جمله است
            if len(text) >= 10 and (" " in text or "‌" in text or "شد" in text):
                return text

        for item in colored.get("red") or []:
            text = (item.get("text") or "").strip()
            if text and len(text) >= 5:
                raise RuntimeError(f"سایت (پیام قرمز): {text}")

        # Phrase fallback even without color detection
        for phrase in SUCCESS_PHRASES:
            if phrase in body:
                # try extract a short visible line containing it
                try:
                    loc = page.locator(f"text=/{re.escape(phrase)}/")
                    if loc.count() > 0 and loc.first.is_visible():
                        return loc.first.inner_text().strip()[:220]
                except Exception:
                    return phrase

        last_hint = page_alert_text(page)
        page.wait_for_timeout(400)

    raise RuntimeError(
        "پیام سبز موفقیت بعد از ذخیره دیده نشد"
        + (f" | {last_hint[:180]}" if last_hint else "")
    )


def click_suspend_if_exists(page) -> bool:
    page.once("dialog", lambda d: d.accept())
    candidates = [
        "input[value='تعلیق']",
        "input[id*='Deleteid']",
        "a:has-text('تعلیق')",
        "button:has-text('تعلیق')",
    ]
    for sel in candidates:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                wait_idle(page, 2000)
                return True
        except Exception:
            continue
    return False


def search_person(page, shenase: str) -> None:
    fill(page, ID["text_part"], shenase)
    click(page, ID["btn_search"])
    wait_idle(page, 3000)


def suspend_with_retry(page, shenase: str, tries: int = 3) -> bool:
    """Search + suspend, retry until button disappears or tries end."""
    for attempt in range(1, tries + 1):
        search_person(page, shenase)
        if not click_suspend_if_exists(page):
            if attempt == 1:
                print(f"  تعلیق: دکمه پیدا نشد (تلاش {attempt}/{tries})")
            else:
                print(f"  تعلیق: هنوز پیدا نشد (تلاش {attempt}/{tries})")
            pause(4.0, "قبل از تلاش مجدد تعلیق")
            continue

        print(f"  تعلیق: کلیک شد (تلاش {attempt})")
        pause(DELAY_AFTER_SUSPEND, "منتظر تکمیل تعلیق")

        # confirm gone
        search_person(page, shenase)
        still = False
        for sel in ["input[value='تعلیق']", "input[id*='Deleteid']"]:
            loc = page.locator(sel)
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    still = True
                    break
            except Exception:
                pass
        if not still:
            print("  تعلیق: تأیید شد (دیگر در لیست نیست)")
            return True
        print("  تعلیق: هنوز در لیست است؛ تکرار می‌شود")
        pause(4.5)
    return False


def ensure_majmoe(page, force: bool = False) -> None:
    try:
        val = page.input_value(ID["ddl_majmoe"])
    except Exception:
        val = ""
    if not force and str(val) == MAJMOE_VALUE:
        return
    select_value(page, ID["ddl_majmoe"], MAJMOE_VALUE)
    pause(DELAY_AFTER_MAJMOE, "بعد از انتخاب نوع زیرمجموعه")


def wait_estelam_ok(page, timeout_ms: int = 25000) -> str:
    page.wait_for_function(
        """() => {
          const el = document.querySelector('#ctl00_ContentPlaceHolder1_DamdarNametxt');
          return el && el.value && el.value.trim().length > 0;
        }""",
        timeout=timeout_ms,
    )
    return page.input_value(ID["damdar_name"]).strip()


def process_one(page, rec: dict) -> None:
    shenase = str(rec["shenase"])
    light = str(int(rec["light_count"]))
    heavy = str(int(rec["heavy_count"]))
    print(f"\n→ {shenase} | {rec.get('name')} | سبک={light} سنگین={heavy}")
    set_status(shenase, "running")

    check_activity_window(page)

    if "AddZirMajmooeKhodEzhari.aspx" not in page.url:
        page.goto(TARGET_URL, wait_until="domcontentloaded")
        wait_idle(page, 1000)

    # 1-2) search + suspend (با تأیید)
    suspended = suspend_with_retry(page, shenase, tries=3)
    if suspended:
        print("  تعلیق: نهایی شد")
    else:
        print("  تعلیق: در لیست پیدا نشد (شاید از قبل معلق بوده)")
        pause(4.0, "قبل از افزودن")

    # 3) majmoe — حتماً قبل از استعلام
    ensure_majmoe(page, force=True)

    # 4) estelam
    fill(page, ID["txt_part_code"], shenase)
    click(page, ID["estelam"])
    try:
        name = wait_estelam_ok(page)
    except PlaywrightTimeout:
        print("  استعلام ناموفق؛ یک‌بار دیگر تعلیق امتحان می‌شود")
        suspend_with_retry(page, shenase, tries=2)
        ensure_majmoe(page, force=True)
        fill(page, ID["txt_part_code"], shenase)
        click(page, ID["estelam"])
        try:
            name = wait_estelam_ok(page)
        except PlaywrightTimeout:
            check_activity_window(page)
            hint = page_alert_text(page)
            raise RuntimeError(
                "بعد از استعلام، نام دامدار پر نشد"
                + (f" | پیام صفحه: {hint[:180]}" if hint else "")
            )

    if not name:
        raise RuntimeError("بعد از استعلام، نام دامدار خالی است")
    print(f"  استعلام: {name}")

    # 5) activity — majmoe را دوباره ست نکن (فرم استعلام پاک می‌شود)
    for _ in range(40):
        if page.locator(ID["ddl_faaliyat"]).is_enabled():
            break
        page.wait_for_timeout(200)
    if page.input_value(ID["ddl_majmoe"]) != MAJMOE_VALUE:
        # فقط اگر از دست رفته بود
        ensure_majmoe(page, force=True)
        fill(page, ID["txt_part_code"], shenase)
        click(page, ID["estelam"])
        name = wait_estelam_ok(page)
    select_value(page, ID["ddl_faaliyat"], ACTIVITY_VALUE)
    pause(DELAY_AFTER_ACTIVITY, "بعد از انتخاب نوع فعالیت")

    # 6) counts + insert
    fill(page, ID["dam_light"], light)
    fill(page, ID["dam_heavy"], heavy)

    pause(DELAY_BEFORE_INSERT, "قبل از ذخیره")
    click(page, ID["insert_btn"])
    wait_idle(page, 2500)

    # فقط با دیدن پیام سبز موفقیت، done می‌شود
    success_msg = confirm_insert_success(page, timeout_sec=14.0)
    set_status(shenase, "done", None)
    print(f"  ✓ انجام شد | {success_msg}")
    pause(3.0, "بعد از موفقیت")


def reload_form(page) -> None:
    """Fresh page so the next record does not inherit a stuck form."""
    print("  رفرش صفحه")
    page.goto(TARGET_URL, wait_until="domcontentloaded")
    wait_idle(page, 2500)
    if "Login.aspx" in page.url:
        ensure_logged_in(page)


def run(shenases: list[str] | None = None) -> None:
    if shenases:
        records = get_records_by_shenase(shenases)
    else:
        records = list_records(status="pending", limit=100000)
    records = [r for r in records if r["status"] in {"pending", "failed"}]
    if not records:
        print("رکوردی برای اجرا نیست.")
        return

    print(f"تعداد رکورد: {len(records)}")
    print("حالت: مرورگر (با پر کردن خودکار یوزر/پسورد)")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            slow_mo=60,
            ignore_https_errors=True,
            args=CHROMIUM_ARGS,
            **LAUNCH_VIEWPORT_KWARGS,
        )
        page = context.pages[0] if context.pages else context.new_page()
        prepare_browser_page(page)
        try:
            ensure_logged_in(page)
            for i, rec in enumerate(records):
                try:
                    if i > 0:
                        pause(DELAY_BETWEEN_RECORDS, "بین رکوردها")
                    process_one(page, rec)
                except Exception as e:
                    set_status(rec["shenase"], "failed", str(e)[:500])
                    print(f"  ✗ خطا: {e}")
                if i < len(records) - 1:
                    try:
                        reload_form(page)
                    except Exception as e:
                        print(f"  رفرش ناموفق: {e}")
            print("\nتمام شد.")
            pause(2.0)
        finally:
            context.close()


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--shenase", action="append", default=[])
    parser.add_argument("--file")
    parser.add_argument("--all-pending", action="store_true")
    args = parser.parse_args()
    shenases = list(args.shenase)
    if args.file:
        shenases.extend(
            [
                ln.strip()
                for ln in Path(args.file).read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
        )
    if not shenases and not args.all_pending:
        print("یا --shenase بده یا --all-pending")
        sys.exit(1)
    run(shenases=shenases or None)


if __name__ == "__main__":
    main()
