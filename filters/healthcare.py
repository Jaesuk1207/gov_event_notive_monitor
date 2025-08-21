import re

# 1) 기관 가중치 분리
PRIMARY_AGENCIES = [
    "보건복지부", "질병관리청", "식품의약품안전처",
    "한국보건산업진흥원", "건강보험심사평가원",
    "국립중앙의료원", "한국보건의료연구원",
]
SECONDARY_AGENCIES = [
    "과학기술정보통신부", "산업통상자원부",
    "정보통신산업진흥원", "정보통신기획평가원",
    "한국지능정보사회진흥원", "한국연구재단",
    "한국산업기술진흥원", "한국산업기술기획평가원",
    "한국에너지기술평가원"
]

INCLUDE_PATTERNS = [
    r"의료기기", r"디지털\s*헬스", r"스마트\s*헬스", r"모바일\s*헬스",
    r"원격(?:의료|진료|모니터링)", r"웨어러블", r"(?:EMR|EHR)", r"의료\s*정보",
    r"임상(?:시험|연구)", r"(?:RWD|RWE)", r"(?:의료\s*AI|AI\s*의료|의료\s*인공지능)",
    r"(?:보건의료\s*데이터|의료\s*데이터)", r"(?:바이오\s*헬스|바이오헬스)",
    r"재활", r"(?:고령자|노인)\s*(?:돌봄|케어)", r"건강\s*관리", r"감염병\s*(?:대응|예방|감시)",
    r"환자|진단|치료|병원|의약품|제약|신약",
    r"(?:보건의료기술|의료기술(?:개발)?)",
    r"(?:바이오[\s·∙ㆍ-]*의료)"# 보강
]

EXCLUDE_PATTERNS = [
    r"토목|건설|도로|철도|항만|선박|조선", r"항공|우주|위성",
    r"국방|군수", r"원자력|원전", r"광산|채굴",
    r"자동차\s*(?:부품|경량화)",
    r"반도체(?!\s*바이오)|디스플레이(?!\s*헬스)|배터리(?!\s*헬스)",
    r"에너지|전력|발전|태양광|풍력|수소",           # 추가
    r"농업|농업기계",                               # 추가
    r"소재부품|산업혁신기반|산단|인프라",            # 추가
    r"양자(컴퓨팅|센싱|플랫폼)"                      # 추가 (필요 시 완화)
]

inc_regex = re.compile("|".join(INCLUDE_PATTERNS), re.IGNORECASE)
exc_regex = re.compile("|".join(EXCLUDE_PATTERNS), re.IGNORECASE)

def _text(*parts):
    return " ".join([p for p in parts if p]).lower()

def score_notice(meta: dict, extra_text: str = "") -> tuple[int, list[str]]:
    reasons = []
    score = 0

    # 1) 기관 가점 (1순위/2순위 분리)
    src_agencies = " ".join([meta.get("소관부처",""), meta.get("전문기관","")])
    is_primary = any(ag in src_agencies for ag in PRIMARY_AGENCIES)
    is_secondary = any(ag in src_agencies for ag in SECONDARY_AGENCIES)

    if is_primary:
        score += 3
        reasons.append("1순위 보건/의료 기관(+3)")
    elif is_secondary:
        score += 1
        reasons.append("2순위 R&D/ICT 기관(+1)")

    # 2) 텍스트 매칭(제목/본문/첨부) - finditer로 문자열 일관 처리
    title = meta.get("공고명","")
    hay = _text(title, extra_text)

    inc = [m.group(0) for m in inc_regex.finditer(hay)]
    exc = [m.group(0) for m in exc_regex.finditer(hay)]

    if inc:
        inc_uniq = {s.lower() for s in inc if s}
        inc_pts = min(4, len(inc_uniq))
        score += inc_pts
        reasons.append(f"헬스 키워드 {inc_uniq}(+{inc_pts})")

    if exc:
        exc_uniq = {s.lower() for s in exc if s}
        exc_pts = min(4, len(exc_uniq))
        score -= exc_pts
        reasons.append(f"비헬스 키워드 {exc_uniq}(-{exc_pts})")

    # 3) 디지털/R&D 맥락 가벼운 가점 (헬스 보조용)
    if re.search(r"R&D|연구개발|플랫폼|데이터|AI|인공지능", hay, re.IGNORECASE):
        score += 1
        reasons.append("디지털/R&D 맥락(+1)")

    # 4) 보수 규칙: 2순위 기관은 헬스 키워드가 없으면 약화
    if is_secondary and not inc:
        # 헬스 맥락 없음 → 점수에서 1점 감산(최소 0 보장 X)
        score -= 1
        reasons.append("2순위 기관인데 헬스 키워드 없음(-1)")

    return score, reasons

def is_interesting_for_association(meta: dict, extra_text: str = "", threshold: int = 5) -> tuple[bool, int, list[str]]:
    score, reasons = score_notice(meta, extra_text)
    return (score >= threshold), score, reasons
