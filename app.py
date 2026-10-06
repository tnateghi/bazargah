from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import (
    count_records,
    get_records_by_shenase,
    init_db,
    list_records,
    mark_many,
    mark_until,
    reset_failed,
    set_status,
    stats,
)
from prepare_queue import build_queue

PAGE_SIZE_OPTIONS = (10, 20, 50, 100, 200)
DEFAULT_PAGE_SIZE = 20
BASE = Path(__file__).parent
UPLOAD_DIR = BASE / "data" / "uploads"
RUN_DIR = BASE / "data" / "runs"
CREDS_FILE = BASE / "data" / "credentials.json"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RUN_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ZirMajmooe Bot Panel")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


def _normalize_page_size(raw: int | None) -> int:
    try:
        size = int(raw or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return size if size in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE


def _load_creds() -> dict:
    if not CREDS_FILE.exists():
        return {"username": "", "password": ""}
    try:
        data = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
        return {
            "username": data.get("username", ""),
            "password": data.get("password", ""),
        }
    except Exception:
        return {"username": "", "password": ""}


@app.on_event("startup")
def _startup():
    init_db()


def _spawn_worker(shenases: list[str]) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    list_file = RUN_DIR / "selected.txt"
    list_file.write_text("\n".join(shenases), encoding="utf-8")
    log_file = RUN_DIR / "worker.log"
    cmd = [sys.executable, "-u", str(BASE / "worker.py"), "--file", str(list_file)]
    with log_file.open("a", encoding="utf-8") as log:
        log.write(f"\n--- spawn {len(shenases)} items ---\n")
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    return subprocess.Popen(cmd, cwd=str(BASE), creationflags=flags).pid


async def _save_upload(file: UploadFile, prefix: str) -> Path:
    suffix = Path(file.filename or "file.bin").suffix or ".bin"
    dest = UPLOAD_DIR / f"{prefix}_{uuid.uuid4().hex[:8]}{suffix}"
    dest.write_bytes(await file.read())
    return dest


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    status: str | None = Query(None),
    q: str | None = Query(None),
    changed: str | None = Query(None),
    msg: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
):
    page_size = _normalize_page_size(page_size)
    s = stats()
    filtered_total = count_records(
        status=status or None, q=q or None, changed=changed or None
    )
    total_pages = max(1, (filtered_total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * page_size
    rows = list_records(
        status=status or None,
        q=q or None,
        changed=changed or None,
        limit=page_size,
        offset=offset,
    )
    start = max(1, page - 2)
    end = min(total_pages, start + 4)
    start = max(1, end - 4)
    page_numbers = list(range(start, end + 1))
    creds = _load_creds()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "stats": s,
            "rows": rows,
            "status": status or "",
            "q": q or "",
            "changed": changed or "",
            "msg": msg or "",
            "page": page,
            "page_size": page_size,
            "page_size_options": PAGE_SIZE_OPTIONS,
            "total_pages": total_pages,
            "filtered_total": filtered_total,
            "page_from": offset + 1 if filtered_total else 0,
            "page_to": min(offset + page_size, filtered_total),
            "page_numbers": page_numbers,
            "creds": creds,
            "progress_pct": round(
                ((s["done"] + s["nochange"]) / s["total"] * 100) if s["total"] else 0,
                1,
            ),
        },
    )


@app.post("/credentials")
def save_credentials(
    username: str = Form(""),
    password: str = Form(""),
):
    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(
        json.dumps(
            {"username": username.strip(), "password": password},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return RedirectResponse("/?msg=یوزر و پسورد ذخیره شد", status_code=303)


@app.post("/load")
async def load_excels(
    stats_file: UploadFile = File(...),
    report_file: UploadFile = File(...),
    activity: str = Form("دام سبك و سنگین روستایی عشایری"),
):
    stats_path = await _save_upload(stats_file, "stats")
    report_path = await _save_upload(report_file, "report")
    result = build_queue(stats_path, report_path, activity or None)
    # شروع از صفر: پیشرفت قبلی و لاگ اجرا پاک شود
    log_file = RUN_DIR / "worker.log"
    selected = RUN_DIR / "selected.txt"
    log_file.write_text("", encoding="utf-8")
    if selected.exists():
        selected.unlink()
    return RedirectResponse(
        f"/?msg=صف از نو ساخته شد: {result['total']} رکورد "
        f"(مانده {result['pending']} · بدون‌تغییر {result['nochange']})",
        status_code=303,
    )


@app.post("/reset-failed")
def reset_failed_route():
    reset_failed()
    return RedirectResponse("/", status_code=303)


@app.post("/mark")
def mark(shenase: str = Form(...), status: str = Form(...)):
    if status in {"pending", "done", "failed"}:
        set_status(shenase, status, None if status != "failed" else "marked manually")
    return RedirectResponse("/", status_code=303)


@app.post("/mark-until")
def mark_until_route(sort_order: int = Form(...)):
    mark_until(sort_order, "done")
    return RedirectResponse("/?status=pending&changed=1", status_code=303)


@app.post("/run")
async def run_selected(request: Request):
    form = await request.form()
    selected = form.getlist("selected")
    mode = form.get("mode", "run")
    if not selected:
        return RedirectResponse("/?status=pending&changed=1", status_code=303)

    if mode == "mark_done":
        mark_many(list(selected), "done", "marked selected")
        return RedirectResponse("/?status=pending&changed=1", status_code=303)

    recs = get_records_by_shenase(list(selected))
    shenases = [r["shenase"] for r in recs if r["status"] in {"pending", "failed"}]
    if not shenases:
        return RedirectResponse("/?status=pending&changed=1", status_code=303)

    for s in shenases:
        set_status(s, "pending", None)
    _spawn_worker(shenases)
    return RedirectResponse("/?status=running", status_code=303)


@app.post("/test-one")
def test_one(shenase: str = Form(...)):
    recs = get_records_by_shenase([shenase])
    if not recs:
        return RedirectResponse("/", status_code=303)
    set_status(shenase, "pending", None)
    _spawn_worker([shenase])
    return RedirectResponse("/?status=running", status_code=303)


@app.get("/api/stats")
def api_stats():
    return stats()


@app.get("/api/worker-log")
def worker_log():
    path = RUN_DIR / "worker.log"
    if not path.exists():
        return JSONResponse({"text": ""})
    text = path.read_text(encoding="utf-8", errors="ignore")
    return JSONResponse({"text": text[-8000:]})


@app.post("/clear-log")
def clear_log():
    path = RUN_DIR / "worker.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return RedirectResponse("/", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8787, reload=False)
