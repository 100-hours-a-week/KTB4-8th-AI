"""네이버 지역 검색 클라이언트 — 장소 검증의 지점명 확인·폐점 판단용 (NAVER API HUB).

API: GET {NAVER_SEARCH_BASE_URL}/search/v1/local?query=<상호>&display=5
     헤더 X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY
     응답 items[].title 에 검색어가 <b> 로 감싸져 온다. 장소 고유 ID 는 없다.
     mapx·mapy 는 경도·위도에 10^7 을 곱한 정수 문자열이다(WGS84, 실측
     '1267623031' → 126.7623031).
"""

import html
import re
from dataclasses import dataclass

import httpx
from langsmith import traceable

from app.core.config import settings

_LOCAL_PATH = "/search/v1/local"
_TAG = re.compile(r"</?b>")


class NaverSearchError(Exception):
    """키 없음·HTTP 오류·시간 초과·응답 형식 오류. 호출하는 쪽은 '확인 못 함'으로 처리한다."""


@dataclass(frozen=True)
class LocalPlace:
    name: str  # 태그를 뺀 상호
    lat: float
    lng: float


@traceable(name="naver_local_search", run_type="tool")
async def search_local(query: str) -> list[LocalPlace]:
    """검색 결과(최대 5개). 결과가 없으면 빈 목록."""
    if not (settings.NAVER_SEARCH_CLIENT_ID and settings.NAVER_SEARCH_CLIENT_SECRET):
        raise NaverSearchError("NAVER_SEARCH_CLIENT_ID, NAVER_SEARCH_CLIENT_SECRET 가 비어 있음")
    headers = {
        "X-NCP-APIGW-API-KEY-ID": settings.NAVER_SEARCH_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": settings.NAVER_SEARCH_CLIENT_SECRET,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.NAVER_SEARCH_BASE_URL, timeout=settings.TIMEOUT_NAVER_SEARCH
        ) as client:
            response = await client.get(_LOCAL_PATH, params={"query": query, "display": 5}, headers=headers)
    except httpx.HTTPError as exc:
        raise NaverSearchError(f"{type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise NaverSearchError(f"HTTP {response.status_code}: {response.text[:200]}")
    try:
        items = response.json().get("items") or []
    except ValueError as exc:
        raise NaverSearchError(f"응답이 JSON 이 아님: {response.text[:200]}") from exc
    try:
        return [
            LocalPlace(
                name=html.unescape(_TAG.sub("", item.get("title") or "")),
                lat=int(item["mapy"]) / 1e7,
                lng=int(item["mapx"]) / 1e7,
            )
            for item in items
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise NaverSearchError(f"좌표 형식이 다름: {items[:1]}") from exc
