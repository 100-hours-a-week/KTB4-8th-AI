"""지도 API 클라이언트 — 주소 → 좌표 (네이버 클라우드 Maps Geocoding).

호출부를 이 파일 하나로 제한해, 벤더를 바꿔도 여기와 config 만 고치면 되고
services/ 는 바뀌지 않게 한다(docs/6 4-4). 외부 연결 지점이 integrations/
에만 있어야 한다는 경계도 이걸로 지킨다(docs/6 6-3).

API: GET {NAVER_MAP_BASE_URL}/map-geocode/v2/geocode?query=<주소>
     헤더 x-ncp-apigw-api-key-id / x-ncp-apigw-api-key
     응답 status, addresses[].x = 경도, addresses[].y = 위도 (둘 다 문자열)
"""

import httpx

from app.core.config import settings
from app.core.exceptions import MapAPIError

_GEOCODE_PATH = "/map-geocode/v2/geocode"


def _new_client() -> httpx.AsyncClient:
    # 호출마다 만든다. 트래픽이 작아(docs/6) 커넥션 재사용 이득이 없고, 공유
    # 클라이언트는 이벤트 루프 수명 관리가 따라붙는다. 테스트에서 이 함수를
    # 바꿔 끼워 가짜 응답을 넣는다.
    return httpx.AsyncClient(base_url=settings.NAVER_MAP_BASE_URL, timeout=settings.TIMEOUT_MAP_API)


async def geocode(address: str) -> tuple[float, float] | None:
    """주소를 (위도, 경도) 로 바꾼다. 주소를 찾지 못하면 None.

    '없는 주소'와 '지도 API 장애'를 구분한다. 전자는 None 을 돌려줘 그 후보만
    빠지게 하고, 후자(키 없음·인증 실패·한도·타임아웃·5xx)는 MapAPIError 로
    요청 전체를 map_api_error(502) 로 만든다 — 그래야 백엔드가 재처리 대상으로
    관리할 수 있다.
    """
    if not (settings.NAVER_MAP_CLIENT_ID and settings.NAVER_MAP_CLIENT_SECRET):
        raise MapAPIError(
            "지도 API 키가 없습니다 — .env 에 NAVER_MAP_CLIENT_ID, NAVER_MAP_CLIENT_SECRET 를 넣어주세요"
        )

    headers = {
        "x-ncp-apigw-api-key-id": settings.NAVER_MAP_CLIENT_ID,
        "x-ncp-apigw-api-key": settings.NAVER_MAP_CLIENT_SECRET,
        "Accept": "application/json",
    }
    try:
        async with _new_client() as client:
            response = await client.get(_GEOCODE_PATH, params={"query": address}, headers=headers)
    except httpx.HTTPError as exc:
        # 타임아웃·연결 실패. Gemini 와 다른 벤더라 원인을 섞지 않게 따로 올린다(docs/6 5-7).
        raise MapAPIError(f"지도 API 호출 실패: {type(exc).__name__}: {exc}") from exc

    if response.status_code != 200:
        # 401·403 은 키 문제, 429 는 지도 쪽 한도, 5xx 는 벤더 장애.
        raise MapAPIError(f"지도 API HTTP {response.status_code}: {response.text[:200]}")

    body = response.json()
    if body.get("status") != "OK":
        raise MapAPIError(f"지도 API 오류: {body.get('status')} {body.get('errorMessage', '')}")

    addresses = body.get("addresses") or []
    if not addresses:
        return None

    # 가장 앞 결과를 쓴다. 검색 단계에서 이미 도로명 주소를 특정해 넘기므로
    # 여러 결과가 나오는 경우는 드물다.
    first = addresses[0]
    return float(first["y"]), float(first["x"])
