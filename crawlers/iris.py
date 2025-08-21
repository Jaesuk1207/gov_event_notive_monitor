import requests
from bs4 import BeautifulSoup
import hashlib
import time

BASE = "https://www.iris.go.kr"
LIST_URL = BASE + "/contents/retrieveBsnsAncmBtinSituListView.do"
VIEW_URL = BASE + "/contents/retrieveBsnsAncmView.do"

# 파일 상단 import 근처에 추가
# 1) id/sn/prg 범용 추출 유틸로 교체
import re

def _extract_iris_args(onclick: str = "", href: str = ""):
    """
    f_bsns...( '015374','1' )     → id=015374, sn=1
    f_bsns...( '015374','ancmIng')→ id=015374, prg=ancmIng
    javascript:...(...)           → 동일 처리
    ...retrieveBsnsAncmView.do?ancmId=...&bsnsAncmSn=... → 쿼리 파싱
    """
    s = (onclick or "").strip()
    if not s and href:
        s = href.strip()
    s = re.sub(r'^\s*javascript:\s*', '', s, flags=re.I)

    m = re.search(r'\((.*?)\)', s)
    if m:
        raw = m.group(1)
        toks = [t.strip().strip('"').strip("'") for t in raw.split(',') if t.strip()]
        ancm_id = None
        sn = None
        prg = None
        for t in toks:
            if t.isdigit():
                if ancm_id is None:
                    ancm_id = t
                elif sn is None:
                    sn = t
            elif t.startswith("ancm"):  # ancmIng / ancmExpct / ancmEnd 등
                prg = t
        if ancm_id:
            return ancm_id, (sn or "1"), prg

    # 쿼리스트링 폴백
    if href:
        m1 = re.search(r'[?&]ancmId=(\d+)', href)
        if m1:
            ancm_id = m1.group(1)
            m2 = re.search(r'[?&]bsnsAncmSn=(\d+)', href)
            sn = m2.group(1) if m2 else "1"
            m3 = re.search(r'[?&]ancmPrg=([a-zA-Z]+)', href)
            prg = m3.group(1) if m3 else None
            return ancm_id, sn, prg

    return None, None, None



def generate_notice_id(link):
    return hashlib.md5(link.encode()).hexdigest()

# 2) 목록 수집 루프 수정 (핵심만)
def fetch_iris_notices(max_pages=3, ancm_prgs=("ancmIng", "ancmExpct")):
    notices, seen = [], set()
    sess = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"}

    for prg in ancm_prgs:
        for page in range(1, max_pages + 1):
            r = sess.get(LIST_URL, params={"pageIndex": page, "ancmPrg": prg}, headers=headers, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            items = soup.select("ul.dbody > li") or soup.select("ul.dbody li")
            if not items:
                continue

            for li in items:
                a = li.select_one("strong.title a") or li.select_one("a")
                if not a:
                    continue

                ancm_id, sn, prg_from_onclick = _extract_iris_args(a.get("onclick", ""), a.get("href", ""))
                if not ancm_id:
                    # 디버깅용: 필요하면 출력 유지
                    # print("[IRIS] no ancmId", a)
                    continue

                # onclick에 prg가 있으면 그것을 우선, 없으면 현재 루프의 prg 사용
                prg_use = prg_from_onclick or prg

                title = a.get_text(strip=True)
                inst_tag = li.select_one("span.inst_title")
                inst = inst_tag.get_text(strip=True) if inst_tag else ""
                date_tag = li.select_one("span.ancmDe")
                date = date_tag.get_text(strip=True).replace("공고일자 :", "") if date_tag else ""

                link = f"{VIEW_URL}?ancmId={ancm_id}&ancmPrg={prg_use}&bsnsAncmSn={sn}"
                nid = hashlib.md5(link.encode()).hexdigest()
                if nid in seen:
                    continue
                seen.add(nid)

                notices.append({
                    "id": nid,
                    "title": title,
                    "institution": inst,
                    "date": date,
                    "link": link,
                    "ancm_id": ancm_id,
                    "bsnsAncmSn": sn,
                    "ancmPrg": prg_use,
                })
            time.sleep(0.2)
    return notices



def _parse_title_area(soup: BeautifulSoup) -> dict:
    result = {}
    for li in soup.select("div.title_area ul.list_dot li.write"):
        strong = li.find("strong")
        span = li.find("span")
        if not strong or not span:
            continue
        key = strong.get_text(strip=True)
        # 줄바꿈/여백 normalize
        val = " ".join(span.get_text(" ", strip=True).split())

        if "소관부처" in key:
            result["소관부처"] = val
        elif "전문기관" in key:
            result["전문기관"] = val
        elif "공고번호" in key:
            result["공고번호"] = val
        elif "공고명" in key:
            result["공고명"] = val
        elif "공고일자" in key:
            result["공고일자"] = val
        elif "접수기간" in key:
            result["접수기간"] = val
    return result


def fetch_notice_metadata_v2(ancm_id: str, ancm_prg: str = "ancmIng", bsnsAncmSn: str = "1") -> dict:
    s = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"{VIEW_URL}?ancmId={ancm_id}&ancmPrg={ancm_prg}",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    params = {"ancmId": ancm_id, "ancmPrg": ancm_prg, "bsnsAncmSn": bsnsAncmSn}
    r = s.get(VIEW_URL, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    data = _parse_title_area(soup)

    if not data or all(v == "" for v in data.values()):
        headers2 = headers.copy()
        headers2["Content-Type"] = "application/x-www-form-urlencoded"
        r2 = s.post(VIEW_URL, data={"ancmId": ancm_id, "bsnsAncmSn": bsnsAncmSn}, headers=headers2, timeout=15)
        r2.raise_for_status()
        r2.encoding = "utf-8"
        soup2 = BeautifulSoup(r2.text, "html.parser")
        data = _parse_title_area(soup2)
    return data



# crawlers/iris.py (일부 추가)
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode, parse_qs, urlparse

IRIS_DETAIL_URL = "https://www.iris.go.kr/contents/retrieveBsnsAncmView.do"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

def _iris_meta_from_link(link: str) -> dict:
    qs = parse_qs(urlparse(link).query)
    ancm_id = (qs.get("ancmId") or [None])[0]
    prg     = (qs.get("ancmPrg") or ["ancmIng"])[0]
    sn      = (qs.get("bsnsAncmSn") or ["1"])[0]
    return fetch_notice_metadata_v2(ancm_id, ancm_prg=prg, bsnsAncmSn=sn) if ancm_id else {}

def fetch_body_and_attachment_text_by_id(ancm_id: str, ancm_prg: str = "ancmIng", bsnsAncmSn: int = 1) -> str:
    """
    상세 페이지에서 본문 텍스트와 첨부파일명을 합쳐 반환.
    JS 렌더링 없이 정적 파싱으로 처리.
    """
    params = {"ancmId": ancm_id, "ancmPrg": ancm_prg, "bsnsAncmSn": str(bsnsAncmSn)}
    url = f"{IRIS_DETAIL_URL}?{urlencode(params)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception:
        return ""

    soup = BeautifulSoup(r.text, "html.parser")
    chunks = []

    # 본문
    body = soup.select_one(".tb_contents .se-contents")
    if body:
        # 텍스트만 추출
        chunks.append(body.get_text(separator=" ", strip=True))

    # 첨부파일명
    for a in soup.select(".add_file_list .add_file li a .text"):
        chunks.append(a.get_text(strip=True))

    return " ".join(chunks)