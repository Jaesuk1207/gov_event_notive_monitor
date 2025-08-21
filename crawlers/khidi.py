# crawlers/khidi.py
from datetime import datetime

# --- 패키지로 실행/단독 실행 모두 지원하는 import 폴백 ---
try:
    # 패키지 컨텍스트( python -m crawlers.khidi )에서
    from .generic_board import BoardConfig, crawl_board
except ImportError:
    # 파일 직접 실행( python crawlers/khidi.py ) 시
    import os, sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from crawlers.generic_board import BoardConfig, crawl_board


def _date_norm(s: str) -> str:
    """
    KHIDI 목록의 '등록일'은 보통 'YYYY-MM-DD' 형태.
    혹시 시간까지 있으면 앞 10자리만 잘라서 표준화합니다.
    """
    s = (s or "").strip().replace(".", "-").replace("/", "-")
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except Exception:
        return s  # 파싱 실패 시 원문 유지(정렬 단계에서 최소값 처리)

def _parse_dt(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return datetime.min  # 날짜 없는 항목은 가장 오래된 것으로 취급

def fetch_khidi_notices(max_pages: int = 2):
    """
    KHIDI 공고 목록 크롤러
    - 페이지 병합 후 '등록일' 기준으로 최신순 정렬
    - BoardConfig에 없는 매개변수(extra_fields 등) 사용 안 함
    """
    # ✔ KHIDI 리스트는 pageNum / rowCnt 사용
    pages = [
        f"/board?menuId=MENU01108&pageNum={i}&rowCnt=20"
        for i in range(1, max_pages + 1)
    ]

    cfg = BoardConfig(
        name="KHIDI",
        base="https://www.khidi.or.kr",
        pages=pages,

        # 행 선택자 (테이블 기반)
        item_selectors=["table tbody tr"],

        # ✔ 제목/링크: td.ellipsis a (첨부 아이콘 a와 구분)
        link_selectors=[
            "td.ellipsis a",              # 가장 정확
            "a[href*='/board/view']",     # 폴백
        ],

        # ✔ 등록일: 4번째 컬럼이 '등록일'
        date_selectors=[
            "td:nth-child(4)",            # KHIDI 목록의 '등록일'
            ".date",                      # 테마 변경 폴백
        ],

        # 제목 텍스트 추출용
        title_selectors=["td.ellipsis a"],

        # 날짜 정규화 함수
        date_normalize=_date_norm,

        # 화면 표시에 쓰일 기관 정보
        institution="보건복지부 > 한국보건산업진흥원",

        # 메타 최소값
        meta_min={
            "소관부처": "보건복지부",
            "전문기관": "한국보건산업진흥원",
        },
    )

    # 1) 페이지별 항목 수집
    items = crawl_board(cfg)

    # 2) 수집 후 등록일 보정 및 최신순 정렬
    for it in items:
        # 크롤러 공통 스키마에 따라 'date' 키를 사용(없으면 빈 문자열)
        it["date"] = _date_norm(it.get("date", ""))

    # 공지(상단고정) 글이 섞여도 최종적으로 날짜 기준으로 정렬
    items.sort(key=lambda it: _parse_dt(it.get("date", "")), reverse=True)

    return items


# ---------------------- 메인 실행부 (단독 테스트용) ----------------------
if __name__ == "__main__":
    import argparse, json

    parser = argparse.ArgumentParser(description="KHIDI 공고 크롤러 테스트")
    parser.add_argument("--pages", type=int, default=1, help="가져올 페이지 수 (기본 1)")
    parser.add_argument("--limit", type=int, default=20, help="출력 개수 제한 (기본 20)")
    parser.add_argument("--json", action="store_true", help="JSON 형태로 출력")
    args = parser.parse_args()

    results = fetch_khidi_notices(max_pages=args.pages)

    # 출력
    if args.json:
        print(json.dumps(results[: args.limit], ensure_ascii=False, indent=2))
    else:
        print(f"[KHIDI] 수집 {len(results)}건 (표시 {min(len(results), args.limit)}건) — 최신순")
        for r in results[: args.limit]:
            date = r.get("date", "")
            title = r.get("title", "")
            link = r.get("link", "")
            print(f"{date} | {title} | {link}")

    # 사용 예:
    #   python crawlers/khidi.py
    #   python crawlers/khidi.py --pages 2 --limit 10
    #   python crawlers/khidi.py --pages 2 --json

