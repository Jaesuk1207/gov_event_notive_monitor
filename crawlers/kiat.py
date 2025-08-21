# crawlers/kiat.py
from __future__ import annotations
import re
from typing import List, Dict, Any, Optional
from datetime import datetime
from urllib.parse import urljoin, quote_plus
import requests
from bs4 import BeautifulSoup
from pathlib import Path

BASE = "https://www.kiat.or.kr"
LIST_PAGE = f"{BASE}/front/board/boardContentsListPage.do"
LIST_AJAX = f"{BASE}/front/board/boardContentsListAjax.do"
VIEW_PAGE = f"{BASE}/front/board/boardContentsViewPage.do"

BOARD_ID = "90"  # 사업공고
MENU_ID  = "b159c9dac684471b87256f1e25404f5e"

def _norm_date(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip().replace(".", "-").replace("/", "-")
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except Exception:
        return (s[:10] or None)

def _parse_dt(s: Optional[str]) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return datetime.min

def _txt(el) -> str:
    return el.get_text(strip=True) if el else ""

def _norm_period(s: str) -> dict:
    raw = (s or "").strip()
    m = re.findall(r"(\d{4}[./-]\d{1,2}[./-]\d{1,2})", raw)
    start = _norm_date(m[0]) if len(m) >= 1 else None
    end   = _norm_date(m[1]) if len(m) >= 2 else None
    pretty = f"{start} ~ {end}" if start or end else raw
    return {"start_date": start, "end_date": end, "period": pretty, "raw": raw}

def make_search_link(title: str) -> str:
    from urllib.parse import quote_plus
    kw = quote_plus(title)
    return (f"{LIST_PAGE}?board_id={BOARD_ID}&MenuId={MENU_ID}"
            f"&srchGubun=TITLE&srchKwd={kw}")


def fetch_kiat_notices(
    max_pages: int = 2,
    timeout: int = 20,
    debug_dir: Optional[Path] = None
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()

    sess = requests.Session()
    common_headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html, */*; q=0.01",
        "Connection": "keep-alive",
    }

    # 초기 진입(쿠키/세션)
    sess.get(
        LIST_PAGE,
        params={"board_id": BOARD_ID, "MenuId": MENU_ID},
        headers={**common_headers, "Referer": BASE},
        timeout=timeout,
    )

    for page in range(1, max_pages + 1):
        try:
            data = {
                "board_id": BOARD_ID,
                "MenuId": MENU_ID,
                "pageIndex": str(page),
                "pageSize": "15",
                "srchGubun": "",
                "srchKwd": "",
            }
            headers = {
                **common_headers,
                "X-Requested-With": "XMLHttpRequest",
                "ajax": "true",
                "Origin": BASE,
                "Referer": f"{LIST_PAGE}?board_id={BOARD_ID}&MenuId={MENU_ID}",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            r = sess.post(LIST_AJAX, data=data, headers=headers, timeout=timeout)
            r.raise_for_status()
            html = r.text

            if debug_dir:
                Path(debug_dir).mkdir(parents=True, exist_ok=True)
                (Path(debug_dir) / f"kiat_p{page}.html").write_text(html, encoding="utf-8")

            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table.list tbody tr")
            if not rows:
                continue

            for tr in rows:
                a = tr.select_one(".td_title a") or tr.find("a")
                if not a:
                    continue

                title = _txt(a)
                href = a.get("href", "")
                m = re.search(r"contentsView\('([^']+)'\)", href)
                contents_id = m.group(1) if m else None

                if contents_id and contents_id in seen:
                    continue
                if contents_id:
                    seen.add(contents_id)

                td_reg  = tr.find("td", class_=lambda c: c and ("td_reg_date" in c or "td_write_date" in c))
                td_term = tr.find("td", class_=lambda c: c and ("td_app_term" in c or "td_app_period" in c))

                reg_date = _norm_date(_txt(td_reg))
                period_info = _norm_period(_txt(td_term))

                # 🔗 상세 뷰 URL(직접 접근 시 차단될 수 있음) + 검색 링크(권장)
                view_link = (f"{VIEW_PAGE}?board_id={BOARD_ID}&MenuId={MENU_ID}&contents_id={contents_id}"
                             if contents_id else urljoin(BASE, href) if href and not href.startswith("javascript:")
                else f"{LIST_PAGE}?board_id={BOARD_ID}&MenuId={MENU_ID}")
                safe_link = make_search_link(title)

                # ✅ 사용자 클릭용: 프록시 링크 우선(원클릭 상세)
                proxy_link = f"/proxy/kiat/{contents_id}?t={quote_plus(title)}" if contents_id else safe_link


                items.append({
                    "source": "KIAT",
                    "title": title,
                    "link": proxy_link,  # ← 여기!
                    "date": reg_date,
                    "institution": "산업통상자원부 > 한국산업기술진흥원",
                    "meta": {
                        "contents_id": contents_id or "",
                        "공고명": title,
                        "공고일자": reg_date or "",
                        "접수기간": period_info["period"],
                        "접수시작": period_info["start_date"] or "",
                        "접수종료": period_info["end_date"] or "",
                        "소관부처": "산업통상자원부",
                        "전문기관": "한국산업기술진흥원",
                        # 🔁 백업 링크: 혹시 프록시가 막히면 UI에서 보조로 노출 가능
                        "backup_links": {
                            "proxy": proxy_link,
                            "view": view_link,
                            "list": f"{LIST_PAGE}?board_id={BOARD_ID}&MenuId={MENU_ID}",
                            "search": safe_link,
                        },
                    },
                })

        except Exception as e:
            if debug_dir:
                Path(debug_dir).mkdir(parents=True, exist_ok=True)
                (Path(debug_dir) / f"kiat_error_p{page}.txt").write_text(str(e), encoding="utf-8")
            continue

    items.sort(key=lambda it: _parse_dt(it.get("date")), reverse=True)
    return items


# ---------------------- 메인 실행부 (단독 테스트용) ----------------------
if __name__ == "__main__":
    import argparse, json

    parser = argparse.ArgumentParser(description="KIAT 사업공고 크롤러 테스트")
    parser.add_argument("--pages", type=int, default=1, help="가져올 페이지 수 (기본 1)")
    parser.add_argument("--limit", type=int, default=20, help="표시 개수 (기본 20)")
    parser.add_argument("--debug-dir", type=str, default="", help="원문 HTML 저장 디렉토리")
    parser.add_argument("--json", action="store_true", help="JSON 형태로 출력")
    args = parser.parse_args()

    dbg = Path(args.debug_dir) if args.debug_dir else None
    data = fetch_kiat_notices(max_pages=args.pages, debug_dir=dbg)

    if args.json:
        print(json.dumps(data[: args.limit], ensure_ascii=False, indent=2))
    else:
        print(f"[KIAT] 수집 {len(data)}건 (표시 {min(len(data), args.limit)}건) — 최신순")
        for row in data[: args.limit]:
            print(f"{row.get('date','')} | {row.get('title','')} | {row.get('link','')}")
