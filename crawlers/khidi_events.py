# crawlers/khidi_events.py
from __future__ import annotations

from datetime import datetime
from typing import List, Dict, Optional
import re
import time

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

BASE = "https://www.khidi.or.kr"
LIST_PATH = "/board?menuId=MENU01491&siteId=SITE00039"  # 기관별 교육·행사

# ── 링크 검증 유틸 ───────────────────────────────────────────────────────────

from urllib.parse import urlparse

PLACEHOLDER_HREFS = {"", "-", "–", "—", "없음", "미정", "n/a", "N/A", "null", "NULL"}

def _is_placeholder_href(href: str | None) -> bool:
    if not href:
        return True
    s = href.strip().lower()
    return s in PLACEHOLDER_HREFS or s == "#" or s.startswith(("javascript:", "mailto:", "tel:"))

def _looks_like_khidi_placeholder(url: str) -> bool:
    try:
        u = urlparse(url)
        host = (u.netloc or "").lower()
        path = (u.path or "").rstrip("/")
        # 의미 없는 KHIDI 내부 더미 경로들
        return ("khidi.or.kr" in host) and (path in ("/board", "/board/-"))
    except Exception:
        return False


from requests.adapters import HTTPAdapter, Retry

# Utilities 가장 위쪽 (fetch_khidi_events 정의보다 위)에 둬요.
def _txt(x):
    return x.get_text(" ", strip=True) if x else ""
import re
from datetime import datetime

def _norm_date(s: str) -> str:
    """여러 형태의 날짜 문자열에서 '첫 날짜'를 뽑아 YYYY-MM-DD로 반환. 실패 시 ''."""
    s = (s or "").strip()
    if not s:
        return ""

    # 1) 범위 표기( ~ — – 至 등)면 첫쪽만 본다
    s = re.split(r"[~–—至]", s)[0]

    # 2) 'YYYY년 M월 D일' 패턴
    m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", s)
    if m:
        y, mo, d = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"

    # 3) 구분자 ., /, - 혼용 패턴
    m = re.search(r"(\d{4})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"

    # 4) ISO 앞부분에서 잘라오기(예: '2025-8-5 14:00')
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.group(1).split("-"))
        return f"{y:04d}-{mo:02d}-{d:02d}"

    # 5) 마지막 시도: 몇 가지 포맷 파싱
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:16], fmt).strftime("%Y-%m-%d")
        except Exception:
            pass

    return ""


def _mount_retries(sess: requests.Session):
    retry = Retry(
        total=2, connect=2, read=2,
        backoff_factor=0.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["HEAD", "GET"]),  # ✅ 리스트→frozenset
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)

def _looks_like_ok_content(resp: requests.Response) -> bool:
    ct = (resp.headers.get("Content-Type") or "").lower()
    if any(x in ct for x in [
        "text/html", "application/pdf", "application/octet-stream",
        "application/msword", "application/vnd.openxmlformats"
    ]):
        return True
    try:
        clen = int(resp.headers.get("Content-Length", "0"))
        if clen > 200:
            return True
    except Exception:
        pass
    return len(resp.content or b"") > 200

from urllib.parse import urljoin

def _safe_abs_url(href: str | None, base: str) -> Optional[str]:
    if _is_placeholder_href(href):
        return None
    return urljoin(base, href.strip())


def _follow_meta_refresh(html: str, base_url: str) -> str | None:
    m = re.search(
        r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\']?[^>]*url\s*=\s*([^"\' >;]+)',
        html, flags=re.I
    )
    if not m:
        return None
    return _safe_abs_url(m.group(1).strip("'\""), base_url)

def _validate_link(url: str, sess: requests.Session, referer: str, timeout=12) -> tuple[bool, dict]:
    """
    HEAD→GET 순으로 검증. 리다이렉트/메타리프레시 추적.
    반환: (ok, meta)  meta: {status, final_url, ct, tried_head, tried_get, note}
    """
    meta = {"tried_head": False, "tried_get": False, "final_url": url}
    if not url:
        meta["note"] = "empty-url"
        return False, meta
    headers = {
        "User-Agent": sess.headers.get("User-Agent", "Mozilla/5.0"),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
        "Referer": referer,
    }
    # 1) HEAD
    try:
        meta["tried_head"] = True
        hr = sess.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        meta.update(status=hr.status_code, final_url=str(hr.url), ct=hr.headers.get("Content-Type"))
        if 200 <= hr.status_code < 300:  # ✅ 2xx만 즉시 성공
            return True, meta
        # 3xx 등은 GET로 계속 확인
    except Exception as e:
        meta["note"] = f"head_err:{e!r}"

    # 2) GET (소량만 확인)
    try:
        meta["tried_get"] = True
        gr = sess.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        meta.update(status=gr.status_code, final_url=str(gr.url), ct=gr.headers.get("Content-Type"))
        if 200 <= gr.status_code < 400 and _looks_like_ok_content(gr):
            if "text/html" in (gr.headers.get("Content-Type") or "").lower():
                nxt = _follow_meta_refresh(gr.text[:5000], str(gr.url))
                if nxt and nxt != str(gr.url):
                    ok2, m2 = _validate_link(nxt, sess, referer, timeout)
                    meta.update({
                        "note": f"meta-refresh→{nxt}",
                        "final_url": m2.get("final_url"),
                        "status": m2.get("status"),
                        "ct": m2.get("ct"),
                    })
                    return ok2, meta
            return True, meta
        meta["note"] = f"get_bad_status:{gr.status_code}"
    except Exception as e:
        meta["note"] = f"get_err:{e!r}"
    return False, meta

# ── 점수/기관명 유틸 ─────────────────────────────────────────────────────────
_KEYWORD_SCORE = (
    ("신청", 5),
    ("바로가기", 4),
    ("원문", 4),
    ("공고문", 3),
    ("자세히", 2),
    ("안내", 1),
)

def _score_anchor(a) -> int:
    txt = (a.get_text(" ", strip=True) or "")
    score = 0
    for kw, w in _KEYWORD_SCORE:
        if kw in txt:
            score += w
    try:
        host = urlparse(a.get("href", "")).netloc.lower()
        if host and "khidi.or.kr" not in host:
            score += 2  # 외부 도메인 가산점
    except Exception:
        pass
    return score

PLACEHOLDERS = {'', '-', '–', '—', '없음', '미정', 'n/a', 'N/A', 'null', 'NULL'}

def _pick_institution(meta: dict) -> Optional[str]:
    for key in ("출처","기관","주최","주관","주최/주관","교육기관","발행기관"):
        val = (meta.get(key) or "").strip()
        if val and val not in PLACEHOLDERS:
            return val
    return None


# ── 상세에서 최적 이동 링크 선택 ─────────────────────────────────────────────
def _pick_best_go_link(detail_html: str, detail_url: str) -> tuple[str, dict]:
    """
    상세 HTML을 보고 이동할 최적 링크와 메타 정보를 함께 반환.
    meta 예: {'출처': '...', 'go_link_type': 'meta:원문링크', 'detail_backup': '...'}
    """
    soup = BeautifulSoup(detail_html, "html.parser")

    # 0) 메타 테이블 수집
    meta: dict[str, str] = {}
    for row in soup.select("table tr"):
        th_el = row.find("th")
        td = row.find("td")
        th = th_el.get_text(strip=True) if th_el else ""
        if not th or not td:
            continue
        meta[th] = td.get_text(" ", strip=True)

    # 1) 메타의 ‘원문/신청’ 링크 우선
    for row in soup.select("table tr"):
        th_el = row.find("th")
        td = row.find("td")
        th = th_el.get_text(strip=True) if th_el else ""
        if not th or not td:
            continue
        if any(k in th for k in ("원문링크", "원문URL", "신청바로가기", "교육신청", "행사바로가기", "참가신청", "접수")):
            a = td.find("a", href=True)
            href = _safe_abs_url(a["href"], detail_url) if a else None
            if href:
                return href, {**meta, "go_link_type": f"meta:{th}", "detail_backup": detail_url}

    # 2) 본문 내 버튼/링크
    body = soup.select_one(".view_cont, .view-contents, .board-view, .board_view, .bbsView, .contents, .boardContent")
    if body:
        cands = []
        for a in body.find_all("a", href=True):
            href = _safe_abs_url(a.get("href", ""), detail_url)
            if not href:
                continue
            sc = _score_anchor(a)
            if sc > 0:
                cands.append((sc, href, a.get_text(" ", strip=True)))
        if cands:
            cands.sort(reverse=True)
            sc, href, txt = cands[0]
            return href, {**meta, "go_link_type": f"body:{txt[:30]}", "detail_backup": detail_url}

    # 3) 첨부 파일(pdf/doc/hwp 우선)
    for a in soup.select(".attach a[href], .file a[href], .add_file a[href], .add_file_list a[href], .downFiles a[href]"):
        href = _safe_abs_url(a.get("href", ""), detail_url)
        if not href:
            continue
        if re.search(r"\.(pdf|hwp|docx?|pptx?)$", href, re.I):
            return href, {**meta, "go_link_type": "attachment", "detail_backup": detail_url}

    # 4) 마지막 폴백: KHIDI 상세
    return detail_url, {**meta, "go_link_type": "detail", "detail_backup": detail_url}

# ── 수집기 ───────────────────────────────────────────────────────────────────
def fetch_khidi_events(max_pages: int = 2, rows: int = 20) -> List[Dict]:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
    })
    _mount_retries(sess)

    out: List[Dict] = []

    for page in range(1, max_pages + 1):
        params = {"pageNum": str(page), "rowCnt": str(rows)}
        r = sess.get(urljoin(BASE, LIST_PATH), params=params, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for tr in soup.select("table tbody tr"):
            a = tr.select_one("td a[href*='/board/view']")
            if not a:
                continue
            title = _txt(a)
            detail_href = urljoin(BASE, a.get("href", ""))

            # 등록일(보통 4번째 컬럼)
            tds = tr.find_all("td")
            reg_date = _norm_date(_txt(tds[3]) if len(tds) >= 4 else "")

            # 상세로 들어가 최적 링크 선택
            dr_html = ""
            try:
                dr = sess.get(detail_href, timeout=20)
                dr.raise_for_status()
                dr_html = dr.text
                cand_link, meta = _pick_best_go_link(dr_html, detail_href)
            except Exception:
                cand_link, meta = (detail_href, {"go_link_type": "detail_error", "detail_backup": detail_href})

            # 1차 후보 검증
            ok, vm = _validate_link(cand_link, sess, referer=detail_href)
            if not ok and dr_html:
                # 후보들 다시 훑어보기
                alt_link = None
                alt_soup = BeautifulSoup(dr_html, "html.parser")
                body = alt_soup.select_one(".view_cont, .view-contents, .board-view, .board_view, .bbsView, .contents, .boardContent")
                cands = []

                if body:
                    for aa in body.find_all("a", href=True):
                        u = _safe_abs_url(aa.get("href", ""), detail_href)
                        if not u or u == cand_link:
                            continue
                        sc = _score_anchor(aa)
                        if sc > 0:
                            cands.append((sc, u, aa.get_text(" ", strip=True)))

                for aa in alt_soup.select(".attach a[href], .file a[href], .add_file a[href], .add_file_list a[href], .downFiles a[href]"):
                    u = _safe_abs_url(aa.get("href", ""), detail_href)
                    if not u or u == cand_link:
                        continue
                    cands.append((3, u, aa.get_text(" ", strip=True)))

                cands.sort(reverse=True)
                for _, u, txt in cands[:5]:
                    ok2, vm2 = _validate_link(u, sess, referer=detail_href)
                    if ok2:
                        alt_link = u
                        meta.update({"go_link_type": f"fallback:{txt[:30]}", "fallback_from": cand_link, "validation": vm2})
                        break

                # ... (alt_link 탐색 후)
                if not alt_link or _looks_like_khidi_placeholder(alt_link):
                    go_link = detail_href
                    meta.update({"go_link_type": "detail_fallback",
                                 "fallback_from": cand_link,
                                 "validation": vm,
                                 "note": "alt_placeholder"})
                else:
                    go_link = alt_link

            else:
                # ok == True 인 경우에도 KHIDI 내부 더미면 detail로 폴백
                if ok and not _looks_like_khidi_placeholder(cand_link):
                    go_link = cand_link
                else:
                    go_link = detail_href
                    meta.setdefault("note", "placeholder-or-bad-candidate")
                meta.update({"validation": vm})

            institution = _pick_institution(meta) or "한국보건산업진흥원"

            out.append({
                "source": "KHIDI_EDU",
                "title": title,
                "link": go_link,
                "date": reg_date,
                "institution": institution,
                "meta": {
                    "목록상_등록일": reg_date,
                    "상세_URL": detail_href,
                    **meta,
                },
            })

            time.sleep(0.05)  # 과도한 요청 방지

    # 최신 등록일 순 정렬
    def _key(x):
        try:
            return datetime.strptime(x.get("date", ""), "%Y-%m-%d")
        except Exception:
            return datetime.min

    out.sort(key=_key, reverse=True)
    return out

if __name__ == "__main__":
    data = fetch_khidi_events(max_pages=2)
    for it in data[:20]:
        print(it["date"], it["title"], "->", it["link"],  it["meta"], "|", it["institution"])
