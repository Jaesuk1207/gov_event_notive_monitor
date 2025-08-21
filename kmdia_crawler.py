import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

BASE_DOMAIN = "https://edu.kmdia.or.kr"
COURSE_PAGE = BASE_DOMAIN + "/GMP/default.asp"
DETAIL_PATH = "/GMP/Document/Course_Request/Course_Introduce_10V.asp"

def fetch_open_courses():
    response = requests.get(COURSE_PAGE)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results = []

    for li in soup.select("div#tab2 ul.swiper-wrapper.comm_swiper li.swiper-slide"):
        course = {}

        # 과정명, 제목, 설명
        cat = li.select_one(".swiper_txt01")
        title = li.select_one(".swiper_title")
        desc = li.select_one(".swiper_txt02")
        if not title:
            continue

        course["category"] = cat.get_text(strip=True) if cat else ""
        course["title"] = title.get_text(strip=True)
        course["description"] = desc.get_text(" ", strip=True) if desc else ""

        # 상세정보 항목 (수강신청, 교육장소, 교육기간, 교육시간)
        for item in li.select(".lec_info li"):
            span = item.select_one("span")
            if span:  # <span>이 있는 항목만 처리
                label = span.get_text(strip=True)
                value = item.get_text(strip=True).replace(label, "").strip()
                if "수강신청" in label:
                    course["application_period"] = value
                elif "교육장소" in label:
                    course["location"] = value
                elif "교육기간" in label:
                    course["period"] = value
                elif "교육시간" in label:
                    course["duration"] = value
            else:
                # <span>이 없는 경우 → 상태값(유료, 모집중, 마감임박 등)
                text = item.get_text(strip=True)
                if text:
                    course.setdefault("status", []).append(text)

        # 상세페이지 URL (fView 파라미터 기반 → 쿼리스트링 추가)
        a_tag = li.select_one("a[href^='javascript:fView']")
        if a_tag:
            m = re.search(r"fView\('(\d+)','(\d+)','(\d+)','(\d+)'\)", a_tag["href"])
            if m:
                sn, year, grade, dseq = m.groups()
                course["url"] = (
                    f"{BASE_DOMAIN}{DETAIL_PATH}"
                    f"?dnSn={sn}&dvYear={year}&dnGrade={grade}&dnDSeq={dseq}"
                )
            else:
                course["url"] = COURSE_PAGE
        else:
            course["url"] = COURSE_PAGE

        results.append(course)

    return results


if __name__ == "__main__":
    for idx, course in enumerate(fetch_open_courses(), 1):
        print(f"\n--- Course #{idx} ---")
        for k, v in course.items():
            print(f"{k}: {v}")



# python kmdia_crawler.py