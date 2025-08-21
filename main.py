# main.py (핵심만 발췌)
from crawlers.g2b_api import fetch_g2b_notices

from pathlib import Path
import json
from crawlers.iris import fetch_iris_notices, fetch_notice_metadata_v2, fetch_body_and_attachment_text_by_id
from crawlers.khidi import fetch_khidi_notices
from crawlers.kiat import fetch_kiat_notices
from crawlers.keit_srome import fetch_keit_srome_notices
from filters.healthcare import is_interesting_for_association
from crawlers.khidi_events import fetch_khidi_events
from crawlers.kmdia import fetch_kmdia_notices #JS
from datetime import datetime #JS

def collect_edu(max_pages=2, out_path: Path | None = None):
    """KHIDI 교육·행사 수집(원문링크 우선)"""
    events = [{"source":"KHIDI_EDU", **n} for n in fetch_khidi_events(max_pages=max_pages)]
    events += [{"source":"KMDIA", "institution":"한국의료기기산업협회", **n} for n in fetch_kmdia_notices()]

    # ✅ 필터링: KMDIA가 아닌데 institution이 "한국의료기기산업협회"로 들어온 경우 제거
    events = [
        ev for ev in events
        if not (
            ev.get("institution") == "한국의료기기산업협회"
            and ev.get("source") != "KMDIA"
        )
    ]
    # 교육·행사는 별도 필터링 없이 전체 저장 (원하면 키워드 필터 추가 가능)
    result = {
        "count": len(events),
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": events,
    }
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    return result

# main.py 상단 근처에 추가
from datetime import datetime

def _parse_date_for_sort(s: str | None) -> datetime:
    if not s:
        return datetime.min
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:16], fmt)
        except Exception:
            pass
    return datetime.min


def enrich_and_filter(notices, fetch_meta_func=None, fetch_extra_text_func=None, threshold=0):
    selected = []
    for n in notices:
        meta = n.get("meta", {})
        if fetch_meta_func:
            try:
                meta = fetch_meta_func(n["link"]) or meta
            except Exception:
                pass

        extra_text = ""
        if fetch_extra_text_func:
            try:
                extra_text = fetch_extra_text_func(n["link"])
            except Exception:
                extra_text = ""

        ok, score, reasons = is_interesting_for_association(meta, extra_text, threshold=threshold)
        if ok:
            selected.append({**n, "meta": meta, "score": score, "reasons": reasons})
    return selected

def _iris_meta_from_link(link: str) -> dict:
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(link).query)
    ancm_id = (qs.get("ancmId") or [None])[0]
    return fetch_notice_metadata_v2(ancm_id, ancm_prg="ancmIng") if ancm_id else {}

def collect(threshold=0, max_pages=3, include_extra=False, out_path: Path | None = None):
    # 1) 각 소스 수집
    iris  = [{"source":"IRIS", **n} for n in fetch_iris_notices(max_pages=max_pages)]
    khidi = [{"source":"KHIDI",**n} for n in fetch_khidi_notices(max_pages=max_pages)]
    kiat  = [{"source":"KIAT", **n} for n in fetch_kiat_notices(max_pages=max_pages)]
    keit  = [{"source":"KEIT", **n} for n in fetch_keit_srome_notices(max_pages=1)]  # 필요 시 API 방식으로 교체
    kmdia = [{"source": "KMDIA", "institution": "한국의료기기산업협회", **n} for n in fetch_kmdia_notices()] #JS

    # --- G2B 추가 ---
    try:
        g2b_items = fetch_g2b_notices(
            max_pages=5,  # 최신 페이지부터 2페이지만 가져오기 (실시간 느낌)
            rows=50,
            days=5,  # 오늘~현재(또는 원하는 일 수)
            query=None,
        )
        g2b = [{"source": "G2B", **n} for n in g2b_items]
    except Exception as e:
        print("[G2B] 수집 실패:", e)
        g2b = []

    # 2) 필터링/스코어
    iris_sel  = enrich_and_filter(iris,  fetch_meta_func=_iris_meta_from_link,
                                  fetch_extra_text_func=(fetch_body_and_attachment_text_by_id if include_extra else None),
                                  threshold=threshold)
    khidi_sel = enrich_and_filter(khidi, threshold=threshold)
    kiat_sel  = enrich_and_filter(kiat,  threshold=threshold)
    keit_sel  = enrich_and_filter(keit,  threshold=threshold)
    g2b_sel = enrich_and_filter(g2b, threshold=threshold)  # ← 추가
    kmdia_sel = enrich_and_filter(kmdia, threshold=threshold) #JS
    

    all_items = iris_sel + khidi_sel + kiat_sel + keit_sel + g2b_sel + kmdia_sel #JS

    # 3) 중복 제거 (제목+날짜+링크)
    seen, dedup = set(), []
    for it in all_items:
        key = (it.get("title","").strip(), it.get("date","").strip(), it.get("link","").strip())
        if key in seen:
            continue
        seen.add(key)
        dedup.append(it)

    # 4) 정렬
    # 기존: dedup.sort(key=lambda x: (-x.get("score",0), x.get("date",""), x.get("title","")))
    dedup.sort(
        key=lambda x: (
            -x.get("score", 0),
            -int(_parse_date_for_sort(x.get("date")).timestamp()),  # ✅ 날짜 내림차순
            x.get("title", ""),
        )
    )
    result = {
        "count": len(dedup),
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": dedup,
        "threshold": threshold,
    }

    # ▼ 여기부터 수정
    if out_path:
        p = Path(out_path)  # ← 문자열이 와도 Path로 변환
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    return result
