# crawlers/kmdia.py
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin
from datetime import datetime

BASE_DOMAIN = "https://edu.kmdia.or.kr"
COURSE_PAGE = BASE_DOMAIN + "/GMP/default.asp"
DETAIL_PATH = "/GMP/Document/Course_Request/Course_Introduce_10V.asp"


def _to_iso_date(s: str | None) -> str | None:
    """여러 포맷의 날짜 문자열을 YYYY-MM-DD로 변환"""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    m = re.search(r'(\d{4})[./-년]\s*(\d{1,2})[./-월]\s*(\d{1,2})', s)
    if m:
        y, mo, d = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    # 이미 ISO-like면 다시 보정
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        y, mo, d = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def fetch_kmdia_notices():
    """한국의료기기산업협회(KMDIA) 모집중인 강의 목록 수집"""
    response = requests.get(COURSE_PAGE)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results = []

    # 모집중인 강의 섹션(tab2) 안의 슬라이드들
    for li in soup.select("div#tab2 ul.swiper-wrapper.comm_swiper li.swiper-slide"):
        cat = li.select_one(".swiper_txt01")
        title = li.select_one(".swiper_title")
        desc = li.select_one(".swiper_txt02")
        if not title:
            continue

        category = cat.get_text(strip=True) if cat else ""
        course_title = title.get_text(strip=True)
        description = desc.get_text(" ", strip=True) if desc else ""

        # 상세정보 항목
        application_period = location = period = duration = ""
        status_list = []
        for item in li.select(".lec_info li"):
            span = item.select_one("span")
            if span:  # 라벨이 있는 경우
                label = span.get_text(strip=True)
                value = item.get_text(strip=True).replace(label, "").strip()
                if "수강신청" in label:
                    application_period = value
                elif "교육장소" in label:
                    location = value
                elif "교육기간" in label:
                    period = value
                elif "교육시간" in label:
                    duration = value
            else:  # 상태 (유료, 모집중, 마감임박 등)
                text = item.get_text(strip=True)
                if text:
                    status_list.append(text)

        # fView() 파라미터에서 상세 URL 만들기
        url = COURSE_PAGE
        a_tag = li.select_one("a[href^='javascript:fView']")
        if a_tag:
            m = re.search(r"fView\('(\d+)','(\d+)','(\d+)','(\d+)'\)", a_tag["href"])
            if m:
                sn, year, grade, dseq = m.groups()
                url = (
                    f"{BASE_DOMAIN}{DETAIL_PATH}"
                    f"?dnSn={sn}&dvYear={year}&dnGrade={grade}&dnDSeq={dseq}"
                )

        # 날짜는 수강신청 시작일 기준 (ISO 변환)
        date = deadline = ""
        if application_period and "~" in application_period:
            parts = application_period.split("~", 1)
            start_raw = parts[0].strip() if parts else ""
            end_raw = parts[1].strip() if len(parts) > 1 else ""
            date = _to_iso_date(start_raw) or start_raw
            deadline = _to_iso_date(end_raw) or end_raw
        else:
            raw = application_period.strip() if application_period else ""
            date = _to_iso_date(raw) or raw

        results.append({
            "title": f"[{category}] {course_title}",
            "date": date,         # ISO 변환된 값
            "deadline": deadline, # 마감일 (있으면)
            "link": url,
            "meta": {
                "description": description,
                "application_period": application_period,
                "location": location,
                "period": period,
                "time": duration,
                "status": ", ".join(status_list),
            }
        })

    return results


# 단독 실행 시 확인용
if __name__ == "__main__":
    for idx, c in enumerate(fetch_kmdia_notices(), 1):
        print(f"\n--- Course #{idx} ---")
        for k, v in c.items():
            print(f"{k}: {v}")
