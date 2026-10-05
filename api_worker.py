"""
HTTP worker: one browser login (captcha), then pure requests.
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from browser_helpers import CHROMIUM_ARGS, LAUNCH_VIEWPORT_KWARGS, prepare_browser_page
from db import get_records_by_shenase, list_records, set_status

BASE = Path(__file__).parent
PROFILE_DIR = BASE / "data" / "browser_profile"
DEBUG_DIR = BASE / "data" / "debug"
LOG_FILE = BASE / "data" / "runs" / "worker.log"
TARGET_URL = "https://sps.bki.ir/Pages/AddZirMajmooeKhodEzhari.aspx"
LOGIN_HINT = "Login.aspx"
ACTIVITY_VALUE = "1114"
MAJMOE_VALUE = "1"

DELAY_AFTER_STEP = 1.2
DELAY_AFTER_SUSPEND = 3.0
DELAY_BEFORE_INSERT = 1.5
DELAY_BETWEEN_RECORDS = 2.5

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def pause(seconds: float, label: str = "") -> None:
    if label:
        print(f"  … صبر {seconds:.1f}s {label}")
    time.sleep(seconds)


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


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values: dict[str, str] = {}
        self._select_name: str | None = None
        self._select_value: str | None = None
        self._option_value: str | None = None
        self._option_selected = False
        self._option_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        if tag == "input":
            name = ad.get("name")
            if not name:
                return
            typ = (ad.get("type") or "text").lower()
            if typ in {"checkbox", "radio"}:
                if "checked" in ad:
                    self.values[name] = ad.get("value", "on")
            else:
                self.values[name] = ad.get("value", "")
        elif tag == "select":
            self._select_name = ad.get("name")
            self._select_value = None
        elif tag == "option" and self._select_name:
            self._option_value = ad.get("value", "")
            self._option_selected = "selected" in ad
            self._option_text = []

    def handle_data(self, data):
        if self._select_name is not None and self._option_value is not None:
            self._option_text.append(data)

    def handle_endtag(self, tag):
        if tag == "option" and self._select_name is not None and self._option_value is not None:
            if self._option_selected:
                self._select_value = self._option_value
            elif self._select_value is None:
                self._select_value = self._option_value
            self._option_value = None
            self._option_selected = False
            self._option_text = []
        elif tag == "select" and self._select_name:
            self.values[self._select_name] = self._select_value or ""
            self._select_name = None
            self._select_value = None


def parse_form(html: str) -> dict[str, str]:
    p = FormParser()
    try:
        p.feed(html)
    except Exception:
        pass
    # unescape values
    return {k: html_lib.unescape(v) for k, v in p.values.items()}


def parse_delta(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    i = 0
    n = len(text)
    while i < n:
        j = text.find("|", i)
        if j < 0:
            break
        try:
            length = int(text[i:j])
        except ValueError:
            break
        i = j + 1
        j = text.find("|", i)
        if j < 0:
            break
        typ = text[i:j]
        i = j + 1
        j = text.find("|", i)
        if j < 0:
            break
        name = text[i:j]
        i = j + 1
        content = text[i : i + length]
        i += length
        if i < n and text[i] == "|":
            i += 1
        if typ == "hiddenField":
            fields[name] = content
        elif typ == "updatePanel":
            fields.update(parse_form(content))
            # keep raw for delete-button search
            fields[f"__PANEL__{name}"] = content
        elif typ == "error":
            fields["__ERROR__"] = f"{name}: {content}"
    return fields


def extract_damdar_name(blob: str) -> str:
    patterns = [
        r'name="ctl00\$ContentPlaceHolder1\$DamdarNametxt"[^>]*value="([^"]*)"',
        r'id="ctl00_ContentPlaceHolder1_DamdarNametxt"[^>]*value="([^"]*)"',
        r'value="([^"]*)"[^>]*name="ctl00\$ContentPlaceHolder1\$DamdarNametxt"',
        r'value="([^"]*)"[^>]*id="ctl00_ContentPlaceHolder1_DamdarNametxt"',
    ]
    for pat in patterns:
        m = re.search(pat, blob, flags=re.I)
        if m and m.group(1).strip():
            return html_lib.unescape(m.group(1).strip())
    return ""


def save_debug(tag: str, content: str) -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"{datetime.now().strftime('%H%M%S')}_{tag}.txt"
    path.write_text(content, encoding="utf-8")
    return path


def ensure_session_cookies(timeout_sec: int = 600) -> requests.Session:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            ignore_https_errors=True,
            args=CHROMIUM_ARGS,
            **LAUNCH_VIEWPORT_KWARGS,
        )
        page = context.pages[0] if context.pages else context.new_page()
        prepare_browser_page(page)
        page.goto(TARGET_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)

        if LOGIN_HINT in page.url:
            print("=" * 60)
            print("کپچا/لاگین لازم است (فقط یک‌بار).")
            print("در مرورگر لاگین کن؛ بعد خودکار کوکی گرفته می‌شود.")
            print("=" * 60)
            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                if LOGIN_HINT not in page.url:
                    break
                page.wait_for_timeout(1500)
            else:
                context.close()
                raise RuntimeError("زمان لاگین تمام شد")
            page.goto(TARGET_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

        if LOGIN_HINT in page.url:
            context.close()
            raise RuntimeError("لاگین برقرار نشد")

        cookies = context.cookies()
        context.close()

    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
        }
    )
    for c in cookies:
        sess.cookies.set(
            c["name"],
            c["value"],
            domain=c.get("domain") or "sps.bki.ir",
            path=c.get("path") or "/",
        )
    return sess


def load_page_state(sess: requests.Session) -> dict[str, str]:
    r = sess.get(TARGET_URL, timeout=60)
    r.raise_for_status()
    if LOGIN_HINT in r.url or LOGIN_HINT in r.text[:3000]:
        raise RuntimeError("نشست لاگین معتبر نیست")
    state = parse_form(r.text)
    if "__VIEWSTATE" not in state:
        raise RuntimeError("ViewState در صفحه پیدا نشد")
    state.setdefault("__VIEWSTATEENCRYPTED", "")
    return state


def find_delete_control(blob: str) -> str | None:
    m = re.search(
        r'name="(ctl00\$ContentPlaceHolder1\$SubTable\$ctl\d+\$Deleteid)"',
        blob,
    )
    return m.group(1) if m else None


def async_post(
    sess: requests.Session,
    state: dict[str, str],
    trigger: str,
    updates: dict[str, str] | None = None,
    button: tuple[str, str] | None = None,
) -> str:
    data = dict(state)
    # drop panel raw caches from payload
    data = {k: v for k, v in data.items() if not k.startswith("__PANEL__") and k != "__ERROR__"}
    if updates:
        data.update(updates)
    data["ctl00$ScriptManager1"] = f"ctl00$ContentPlaceHolder1$UpdatePanel1|{trigger}"
    data["__ASYNCPOST"] = "true"
    data.setdefault("__EVENTTARGET", "")
    data.setdefault("__EVENTARGUMENT", "")
    data.setdefault("__LASTFOCUS", "")
    data.setdefault("__VIEWSTATEENCRYPTED", "")
    if button:
        data[button[0]] = button[1]

    headers = {
        "X-MicrosoftAjax": "Delta=true",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://sps.bki.ir",
        "Referer": TARGET_URL,
        "Cache-Control": "no-cache",
    }
    r = sess.post(TARGET_URL, data=data, headers=headers, timeout=60)
    r.raise_for_status()
    text = r.text
    if "pageRedirect||" in text and "Login.aspx" in text:
        raise RuntimeError("نشست منقضی شد؛ دوباره لاگین لازم است")

    parsed = parse_delta(text)
    if "__ERROR__" in parsed:
        raise RuntimeError(parsed["__ERROR__"])
    # update state with hidden fields + inputs from panels
    for k, v in parsed.items():
        state[k] = v
    # also try direct name extraction from whole response
    nm = extract_damdar_name(text)
    if nm:
        state["ctl00$ContentPlaceHolder1$DamdarNametxt"] = nm

    pause(DELAY_AFTER_STEP)
    return text


def process_one(sess: requests.Session, state: dict[str, str], rec: dict) -> dict[str, str]:
    shenase = str(rec["shenase"])
    light = str(int(rec["light_count"]))
    heavy = str(int(rec["heavy_count"]))
    print(f"\n→ {shenase} | {rec.get('name')} | سبک={light} سنگین={heavy}")
    set_status(shenase, "running")

    # 1) search existing
    text = async_post(
        sess,
        state,
        "ctl00$ContentPlaceHolder1$BtnSearch",
        updates={
            "ctl00$ContentPlaceHolder1$TextpartId": shenase,
            "ctl00$ContentPlaceHolder1$txtPartidCode": state.get(
                "ctl00$ContentPlaceHolder1$txtPartidCode", ""
            ),
        },
        button=("ctl00$ContentPlaceHolder1$BtnSearch", "جستجو شناسه یکتا"),
    )
    panels = "".join(v for k, v in state.items() if k.startswith("__PANEL__"))
    del_ctrl = find_delete_control(text) or find_delete_control(panels)

    # 2) suspend
    if del_ctrl:
        async_post(
            sess,
            state,
            del_ctrl,
            updates={"ctl00$ContentPlaceHolder1$TextpartId": shenase},
            button=(del_ctrl, "تعلیق"),
        )
        print("  تعلیق: شد")
        pause(DELAY_AFTER_SUSPEND, "بعد از تعلیق، قبل از افزودن")
    else:
        print("  تعلیق: یافت نشد/لازم نبود")
        pause(1.0)

    # 3) majmoe
    async_post(
        sess,
        state,
        "ctl00$ContentPlaceHolder1$ddlMajmoe",
        updates={
            "__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlMajmoe",
            "ctl00$ContentPlaceHolder1$ddlMajmoe": MAJMOE_VALUE,
            "ctl00$ContentPlaceHolder1$TextpartId": shenase,
        },
    )

    # 4) estelam — مهم: هم TextpartId و هم txtPartidCode
    text = async_post(
        sess,
        state,
        "ctl00$ContentPlaceHolder1$Estelam",
        updates={
            "ctl00$ContentPlaceHolder1$ddlMajmoe": MAJMOE_VALUE,
            "ctl00$ContentPlaceHolder1$TextpartId": shenase,
            "ctl00$ContentPlaceHolder1$txtPartidCode": shenase,
            "ctl00$ContentPlaceHolder1$DDLFaaliyat": "0",
            "ctl00$ContentPlaceHolder1$DamdarNametxt": "",
            "ctl00$ContentPlaceHolder1$DamdarIDtxt": "",
            "ctl00$ContentPlaceHolder1$DamLightTxt": "",
            "ctl00$ContentPlaceHolder1$DamHeavyTxt": "",
        },
        button=("ctl00$ContentPlaceHolder1$Estelam", "استعلام شناسه"),
    )

    name = (state.get("ctl00$ContentPlaceHolder1$DamdarNametxt") or "").strip()
    if not name:
        name = extract_damdar_name(text)
    if not name:
        # one retry after short wait + page refresh state
        pause(2.0, "تلاش مجدد استعلام")
        state = load_page_state(sess)
        text = async_post(
            sess,
            state,
            "ctl00$ContentPlaceHolder1$Estelam",
            updates={
                "ctl00$ContentPlaceHolder1$ddlMajmoe": MAJMOE_VALUE,
                "ctl00$ContentPlaceHolder1$TextpartId": shenase,
                "ctl00$ContentPlaceHolder1$txtPartidCode": shenase,
            },
            button=("ctl00$ContentPlaceHolder1$Estelam", "استعلام شناسه"),
        )
        name = (state.get("ctl00$ContentPlaceHolder1$DamdarNametxt") or "").strip() or extract_damdar_name(text)

    if not name:
        dbg = save_debug(f"estelam_fail_{shenase}", text)
        raise RuntimeError(f"بعد از استعلام، نام دامدار پر نشد (لاگ: {dbg.name})")

    phone = state.get("ctl00$ContentPlaceHolder1$TelephoneTxt", "")
    print(f"  استعلام: {name} | موبایل={phone or '-'}")

    # 5) activity
    async_post(
        sess,
        state,
        "ctl00$ContentPlaceHolder1$DDLFaaliyat",
        updates={
            "__EVENTTARGET": "ctl00$ContentPlaceHolder1$DDLFaaliyat",
            "ctl00$ContentPlaceHolder1$ddlMajmoe": MAJMOE_VALUE,
            "ctl00$ContentPlaceHolder1$TextpartId": shenase,
            "ctl00$ContentPlaceHolder1$txtPartidCode": shenase,
            "ctl00$ContentPlaceHolder1$DamdarNametxt": name,
            "ctl00$ContentPlaceHolder1$DDLFaaliyat": ACTIVITY_VALUE,
        },
    )

    pause(DELAY_BEFORE_INSERT, "قبل از ذخیره")

    # 6) insert
    async_post(
        sess,
        state,
        "ctl00$ContentPlaceHolder1$InsertZirMajmooeBtn",
        updates={
            "ctl00$ContentPlaceHolder1$ddlMajmoe": MAJMOE_VALUE,
            "ctl00$ContentPlaceHolder1$TextpartId": shenase,
            "ctl00$ContentPlaceHolder1$txtPartidCode": shenase,
            "ctl00$ContentPlaceHolder1$DamdarNametxt": name,
            "ctl00$ContentPlaceHolder1$DDLFaaliyat": ACTIVITY_VALUE,
            "ctl00$ContentPlaceHolder1$DamLightTxt": light,
            "ctl00$ContentPlaceHolder1$DamHeavyTxt": heavy,
        },
        button=("ctl00$ContentPlaceHolder1$InsertZirMajmooeBtn", "افزودن به زیرمجموعه"),
    )
    set_status(shenase, "done", None)
    print("  ✓ انجام شد")
    return state


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
    print("حالت: درخواست HTTP (مرورگر فقط برای لاگین/کپچا)")
    sess = ensure_session_cookies()
    state = load_page_state(sess)
    print("صفحه و ViewState آماده شد.")

    for i, rec in enumerate(records):
        try:
            if i > 0:
                pause(DELAY_BETWEEN_RECORDS, "بین رکوردها")
            state = process_one(sess, state, rec)
            if (i + 1) % 15 == 0:
                state = load_page_state(sess)
        except Exception as e:
            set_status(rec["shenase"], "failed", str(e)[:500])
            print(f"  ✗ خطا: {e}")
            try:
                state = load_page_state(sess)
            except Exception as e2:
                print(f"  نشست از دست رفت: {e2}")
                sess = ensure_session_cookies()
                state = load_page_state(sess)
    print("\nتمام شد.")


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
