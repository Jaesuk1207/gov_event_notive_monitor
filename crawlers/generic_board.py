# crawlers/generic_board.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NoticeBot/1.0; +local)"
}

@dataclass
class BoardConfig:
    name: str
    base: str                          # 예) "https://www.khidi.or.kr"
    pages: List[str]                   # 목록 페이지 경로(상대/절대)
    item_selectors: Sequence[str]      # 아이템 후보 셀렉터(여러 개)
    link_selectors: Sequence[str]      # 아이템 내부 링크 후보 셀렉터
    date_selectors: Sequence[str]      # 아이템 내부 날짜 후보 셀렉터
    title_selectors: Sequence[str]     # 아이템 내부 제목 후보 셀렉터(보통 a 텍스트면 빈 배열)
    date_normalize: Optional[Callable[[str], str]] = None
    institution: str = ""              # "소관 > 전문기관" 표기
    meta_min: dict = None              # {"소관부처":..., "전문기관":...}

def _get(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")

def _first(soup_or_tag, selectors: Sequence[str]):
    """여러 셀렉터 중 첫 매칭을 반환"""
    for sel in selectors:
        if not sel:
            continue
        found = soup_or_tag.select_one(sel)
        if found:
            return found
    return None

def crawl_board(cfg: BoardConfig, sleep_sec: float = 0.8) -> List[dict]:
    results = []
    for pg in cfg.pages:
        url = urljoin(cfg.base, pg)
        soup = _get(url)

        # 아이템 후보 셀렉터들 중 매칭되는 것을 순차 시도
        items = []
        for sel in cfg.item_selectors:
            items = soup.select(sel)
            if items:
                break

        for it in items:
            # 링크 & 제목
            a = _first(it, cfg.link_selectors)
            if not a:
                continue
            href = (a.get("href") or "").strip()
            link = urljoin(cfg.base, href)

            title = a.get_text(strip=True)
            # 필요 시 제목 별도 셀렉터 우선
            tnode = _first(it, cfg.title_selectors)
            if tnode:
                title = tnode.get_text(strip=True) or title

            # 날짜
            date = ""
            dnode = _first(it, cfg.date_selectors)
            if dnode:
                raw = dnode.get_text(strip=True)
                date = cfg.date_normalize(raw) if cfg.date_normalize else raw

            results.append({
                "title": title,
                "link": link,
                "date": date,
                "institution": cfg.institution,
                "meta": {
                    "공고명": title,
                    **(cfg.meta_min or {}),
                    "공고일자": date,
                    "접수기간": "-",  # 필요 시 상세 파싱으로 보강
                },
            })

        time.sleep(sleep_sec)
    return results
