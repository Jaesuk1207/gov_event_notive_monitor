# app.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List

import requests
from dotenv import load_dotenv
from flask import (
    Flask, Response, jsonify, redirect, render_template, request, url_for,
)
from zoneinfo import ZoneInfo

# ──────────────────────────────────────────────────────────────────────────────
# TZ / ENV
# ──────────────────────────────────────────────────────────────────────────────
KST = ZoneInfo("Asia/Seoul")
load_dotenv()

# naive timestamp를 무엇으로 간주할지(UTC or KST)
ASSUME_GEN_AT_UTC = os.getenv("ASSUME_GEN_AT_UTC", "1").lower() not in ("0", "false", "no")

# ──────────────────────────────────────────────────────────────────────────────
# Paths / App
# ──────────────────────────────────────────────────────────────────────────────
BASE        = Path(__file__).resolve().parent
TEMPLATES   = BASE / "templates"
STATIC      = BASE / "static"
DATA_DIR    = BASE / "data"
RESULTS_JSON= DATA_DIR / "results.json"
EVENTS_JSON = DATA_DIR / "events.json"

app = Flask(__name__, template_folder=str(TEMPLATES), static_folder=str(STATIC))

DISPLAY_ALIAS = {
    "G2B": "나라장터(식약처)",
}

# ──────────────────────────────────────────────────────────────────────────────
# Time helpers
# ──────────────────────────────────────────────────────────────────────────────
def _file_mtime_kst(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, KST).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def _parse_any_dt(s: str) -> datetime | None:
    """ISO/오프셋/밀리초/스페이스/슬래시 등 웬만한 문자열을 datetime으로.
       tz가 있으면 그대로, 없으면 None tz로 돌려줌(후처리에서 가정)."""
    raw = s.strip()
    if not raw:
        return None

    # 1) ISO 8601 (+오프셋/밀리초/'Z') 시도
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass

    # 2) 공백 구분/슬래시/마침표 → ISO 비슷하게 정리
    guess = raw.replace("T", " ").replace("/", "-")
    # 밀리초가 붙어 있으면 잘라냄 (예: 2025-08-11 03:17:50.123)
    if "." in guess:
        # ".123+09:00" 같은 형태도 고려하여 초 뒤로 잘라냄
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", guess)
        if m:
            guess = m.group(1)

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(guess, fmt)
        except Exception:
            continue

    return None

def _to_kst_display(s: str | None, fallback_path: Path | None = None) -> str | None:
    """문자열 s를 KST로 보기좋게. tz 없으면 환경변수 정책에 따라 UTC 또는 KST로 간주."""
    if s:
        dt = _parse_any_dt(str(s))
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc if ASSUME_GEN_AT_UTC else KST)
            return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")

    # 파싱 실패 → 파일 mtime(KST) 폴백
    if fallback_path is not None:
        mt = _file_mtime_kst(fallback_path)
        if mt:
            return mt
    return None

# ──────────────────────────────────────────────────────────────────────────────
# JSON helpers / search helpers
# ──────────────────────────────────────────────────────────────────────────────
_DATE_FMTS = ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d")

def _atomic_write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    bak = path.with_suffix(path.suffix + ".bak")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    if path.exists():
        try:
            path.replace(bak)
        except Exception:
            try:
                bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                path.unlink(missing_ok=True)
            except Exception:
                pass
    tmp.replace(path)

def _load_json_safe(path: Path, fallback: dict) -> dict:
    def _read(p: Path):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    if path.exists() and path.stat().st_size > 0:
        for _ in range(3):
            try:
                return _read(path)
            except json.JSONDecodeError:
                time.sleep(0.15)
            except Exception:
                break
    bak = path.with_suffix(path.suffix + ".bak")
    if bak.exists() and bak.stat().st_size > 0:
        try:
            return _read(bak)
        except Exception:
            pass
    return dict(fallback)

def load_results() -> dict:
    return _load_json_safe(RESULTS_JSON, {"count": 0, "generated_at": None, "items": [], "threshold": None})

def load_events() -> dict:
    return _load_json_safe(EVENTS_JSON, {"count": 0, "generated_at": None, "items": []})

def haystack(item: dict) -> str:
    meta = item.get("meta", {}) or {}
    parts = [
        item.get("source", ""), item.get("title", ""), item.get("institution", ""), item.get("link", ""),
        ", ".join(item.get("reasons", [])),
        *[str(v) for v in meta.values()],
    ]
    return " ".join(parts).lower()

def _pick_date_str(item: dict) -> str | None:
    if item.get("date"):
        return str(item["date"])
    meta = item.get("meta") or {}
    for k in ("공고일자", "등록일", "게시일"):
        if meta.get(k):
            return str(meta[k])
    if meta.get("notice_posted_at"):
        return str(meta["notice_posted_at"])
    term = str(meta.get("접수기간") or "")
    m = re.search(r"(\d{4}[./-]\d{1,2}[./-]\d{1,2})", term)
    return m.group(1) if m else None

def _parse_date_for_sort(item: dict) -> datetime:
    s = _pick_date_str(item)
    if not s:
        return datetime.min
    s = s.strip()
    s = re.split(r"\s*~\s*", s)[0]
    s = s.replace(".", "-").replace("/", "-")
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s[:16], fmt)
        except Exception:
            continue
    return datetime.min

# ──────────────────────────────────────────────────────────────────────────────
# Views
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/")
def index():
    data = load_results()
    disp_min = int(request.args.get("screen_min", 0))
    q = request.args.get("q", "").strip().lower()

    base_items = data.get("items", [])
    shown = [i for i in base_items if i.get("score", 0) >= disp_min]
    if q:
        shown = [i for i in shown if q in haystack(i)]

    groups_map: Dict[str, List[dict]] = defaultdict(list)
    for it in shown:
        src = (it.get("source") or "OTHER").upper()
        groups_map[src].append(it)

    for items in groups_map.values():
        items.sort(key=lambda x: (-int(_parse_date_for_sort(x).timestamp()), -x.get("score", 0), x.get("title", "")))

    order = ["IRIS", "KEIT", "KIAT", "KHIDI", "G2B", "OTHER"]
    groups: List[dict] = []
    for src in order:
        items = groups_map.get(src, [])
        if items:
            groups.append({"source": DISPLAY_ALIAS.get(src, src), "count": len(items), "items": items})
    for src, items in groups_map.items():
        if src not in order:
            groups.append({"source": DISPLAY_ALIAS.get(src, src), "count": len(items), "items": items})

    gen_at = _to_kst_display(data.get("generated_at"), RESULTS_JSON)
    stats = {
        "generated_at": gen_at,
        "count_total": len(base_items),
        "count_shown": len(shown),
        "avg_score": round(sum(i.get("score", 0) for i in shown) / len(shown), 2) if shown else 0.0,
        "file_threshold": data.get("threshold"),
        "is_refreshing": _FLAGS["all"],
    }
    params = {"screen_min": disp_min, "q": q}
    return render_template("index.html", groups=groups, stats=stats, params=params)

@app.get("/api/notices")
def api_notices():
    return jsonify(load_results())

@app.get("/edu")
def edu():
    data = load_events()
    q = request.args.get("q", "").strip().lower()

    items = data.get("items", [])
    if q:
        def _hay(i: dict) -> str:
            meta = i.get("meta") or {}
            return " ".join([i.get("title",""), i.get("institution",""), i.get("link","")] + [str(v) for v in meta.values()]).lower()
        items = [x for x in items if q in _hay(x)]

    items.sort(key=lambda x: (x.get("date",""), x.get("title","")), reverse=True)
    gen_at = _to_kst_display(data.get("generated_at"), EVENTS_JSON)
    stats = {"generated_at": gen_at, "count": len(items), "is_refreshing": _FLAGS["events"]}
    return render_template("events.html", items=items, stats=stats, params={"q": q})

@app.get("/api/events")
def api_events():
    return jsonify(load_events())

# ──────────────────────────────────────────────────────────────────────────────
# Collectors
# ──────────────────────────────────────────────────────────────────────────────
def run_collection(threshold: int = 0, max_pages: int = 10):
    import sys
    sys.path.append(str(BASE.parent))
    from main import collect

    tmp_out = RESULTS_JSON.with_suffix(RESULTS_JSON.suffix + ".tmp_collect")
    print(f"[REFRESH] collecting notices → {tmp_out}")
    payload = collect(threshold=threshold, max_pages=max_pages, include_extra=False, out_path=str(tmp_out))
    if tmp_out.exists():
        try:
            payload = json.loads(tmp_out.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not payload:
        payload = {"count": 0, "generated_at": None, "items": [], "threshold": threshold}
    _atomic_write_json(RESULTS_JSON, payload)
    tmp_out.unlink(missing_ok=True)
    print(f"[REFRESH] notices count = {payload.get('count')}")

def run_events_collection(max_pages: int = 2):
    import sys
    sys.path.append(str(BASE.parent))
    from main import collect_edu

    tmp_out = EVENTS_JSON.with_suffix(EVENTS_JSON.suffix + ".tmp_collect")
    print(f"[REFRESH] collecting events → {tmp_out}")
    payload = collect_edu(max_pages=max_pages, out_path=tmp_out)  # Path 객체 그대로 넘겨도 됨
    if tmp_out.exists():
        try:
            payload = json.loads(tmp_out.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not payload:
        payload = {"count": 0, "generated_at": None, "items": []}
    _atomic_write_json(EVENTS_JSON, payload)
    tmp_out.unlink(missing_ok=True)
    print(f"[REFRESH] events count = {payload.get('count')}")

# ──────────────────────────────────────────────────────────────────────────────
# Refresh endpoints (distinct endpoints to avoid collisions)
# ──────────────────────────────────────────────────────────────────────────────
_FLAGS = {"all": False, "notices": False, "events": False}

@app.post("/refresh", endpoint="refresh_all")
def refresh_all():
    if not _FLAGS["all"]:
        _FLAGS["all"] = True
        def _job():
            try:
                run_collection(threshold=0, max_pages=10)
                run_events_collection(max_pages=2)
            finally:
                time.sleep(0.5)
                _FLAGS["all"] = False
        threading.Thread(target=_job, daemon=True).start()
    return redirect(url_for("index"))

@app.post("/refresh/notices", endpoint="refresh_notices")
def refresh_notices():
    if not _FLAGS["notices"]:
        _FLAGS["notices"] = True
        def _job():
            try:
                run_collection(threshold=0, max_pages=10)
            finally:
                time.sleep(0.5)
                _FLAGS["notices"] = False
        threading.Thread(target=_job, daemon=True).start()
    return redirect(url_for("index"))

@app.post("/refresh/events", endpoint="refresh_events")
def refresh_events():
    if not _FLAGS["events"]:
        _FLAGS["events"] = True
        def _job():
            try:
                run_events_collection(max_pages=2)
            finally:
                time.sleep(0.5)
                _FLAGS["events"] = False
        threading.Thread(target=_job, daemon=True).start()
    return redirect(url_for("edu"))

def _auto_refresh_loop():
    while True:
        try:
            run_collection(threshold=0, max_pages=10)
            run_events_collection(max_pages=2)
        except Exception as e:
            print("[AUTO-REFRESH ERROR]", e)
        time.sleep(15 * 60)

# ──────────────────────────────────────────────────────────────────────────────
# KIAT proxy (상세 보기 경유 시 alert 차단/정적 경로 고정)
# ──────────────────────────────────────────────────────────────────────────────
def _inject_mute_alerts(html: str) -> str:
    snippet = "<script>try{window.alert=function(){};}catch(e){}</script>"
    return re.sub(r"</head>", snippet + "</head>", html, 1, flags=re.I) if re.search(r"</head>", html, flags=re.I) else snippet + html

@app.get("/proxy/kiat/<contents_id>")
def proxy_kiat(contents_id):
    HOST      = "https://www.kiat.or.kr"
    LIST_PAGE = f"{HOST}/front/board/boardContentsListPage.do"
    VIEW_DO   = f"{HOST}/front/board/boardContentsView.do"
    BOARD_ID  = "90"
    MENU_ID   = "b159c9dac684471b87256f1e25404f5e"

    title = request.args.get("t", "")

    sess = requests.Session()
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"),
        "Accept-Language": "ko-KR,ko;q=0.9",
    }

    def _fix_static_paths(html: str) -> str:
        return re.sub(r'((?:href|src)=["\'])/(?!/)', r'\1https://www.kiat.or.kr/', html, flags=re.I)

    try:
        ref = f"{LIST_PAGE}?board_id={BOARD_ID}&MenuId={MENU_ID}"
        sess.get(LIST_PAGE, params={"board_id": BOARD_ID, "MenuId": MENU_ID}, headers={**headers, "Referer": HOST}, timeout=20).raise_for_status()
        payload = {
            "miv_pageNo": "1", "miv_pageSize": "15", "total_cnt": "", "LISTOP": "",
            "mode": "W", "contents_id": contents_id, "board_id": BOARD_ID,
            "cate_id": "", "field_id": "", "intropage_boardUseYn": "", "MenuId": MENU_ID, "state_filter": "W",
        }
        pr = sess.post(VIEW_DO, data=payload, headers={**headers, "Origin": HOST, "Referer": ref, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}, timeout=20, allow_redirects=True)
        pr.raise_for_status()
        html = pr.text
        if ('id="contentsList"' in html) or ("/front/board/boardContentsListAjax.do" in html):
            from urllib.parse import quote_plus
            kw = quote_plus(title) if title else ""
            return redirect(ref + (f"&srchGubun=TITLE&srchKwd={kw}" if kw else ""), code=302)
        return Response(_inject_mute_alerts(_fix_static_paths(html)), mimetype="text/html")
    except Exception:
        from urllib.parse import quote_plus
        kw = quote_plus(title) if title else ""
        return redirect(f"{LIST_PAGE}?board_id={BOARD_ID}&MenuId={MENU_ID}" + (f"&srchGubun=TITLE&srchKwd={kw}" if kw else ""), code=302)

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=_auto_refresh_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, debug=True, use_reloader=False)
