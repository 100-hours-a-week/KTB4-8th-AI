from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.common import APIResponse


#요청 스키마 (docs/1 2-4)
class VerifyPlaceRequest(BaseModel):
    place_name : str = Field(description = "검증할 장소명")
    region : Optional[str] = Field(default = None, description = "지역 (검색 정확도 보완용)")
    summary : Optional[str] = Field(default = None, description = "장소 특징 요약 (검색 정확도 보완용)")
    event_start_date : Optional[date] = Field(default = None, description = "analyze-video가 찾은 행사 시작일 (없으면 null)")
    event_end_date : Optional[date] = Field(default = None, description = "analyze-video가 찾은 행사 종료일 (없으면 null)")


#응답 스키마 (docs/1 2-4)
class PlaceCandidate(BaseModel):
    place_name : str = Field(description = "검색된 장소명")
    address : str = Field(description = "정규 주소")
    business_hours : str = Field(description = "영업시간")
    lat : float = Field(description = "위도")
    lng : float = Field(description = "경도")
    event_start_date : Optional[date] = Field(default = None, description = "확정된 행사 시작일 (기간 한정 행사가 아니면 null)")
    event_end_date : Optional[date] = Field(default = None, description = "확정된 행사 종료일 (기간 한정 행사가 아니면 null)")
    event_period_unresolved : bool = Field(description = "기간 한정 행사로 보이나 종료일을 끝내 확인하지 못해 사용자 확인이 필요한지 여부")


class VerifyPlaceData(BaseModel):
    exists : bool = Field(description = "장소 실존 여부")
    candidates : list[PlaceCandidate] = Field(description = "검증된 장소 후보 목록 (신뢰도순, 1번째가 자동 채택값)")


VerifyPlaceResponse = APIResponse[VerifyPlaceData]


# LLM 전용. 웹검색 결과를 옮겨 담는 형식이다. 좌표는 LLM 이 아니라 지도 API 가
# 채운다 — 모델이 적은 위경도는 그럴듯한 숫자를 지어낼 위험이 크다.
class GroundedPlace(BaseModel):
    # 동일성 검증용. 맨 앞에 두는 게 핵심이다 — 필드 순서가 모델의 작업 순서라,
    # 근거를 먼저 적게 해야 실제로 확인한다(screen_text 와 같은 원리). 이 칸이
    # 없을 땐 "실존하지만 영상 속 가게가 아닌 곳"이 그대로 통과했다. 응답으로는
    # 나가지 않는다.
    evidence : str = Field(description = "찾던 장소의 특징 중 무엇이 이 가게에 있는지, 검색 결과의 어디서 확인했는지. 확인 못 했으면 빈 문자열")
    place_name : str = Field(description = "검색으로 확인한 정확한 상호 (지점명 포함)")
    address : str = Field(description = "도로명 주소. 좌표 조회에 그대로 쓰이므로 가능한 한 완전하게")
    business_hours : str = Field(description = "영업시간 원문. 검색으로 못 찾으면 '확인 불가'")
    event_start_date : Optional[date] = Field(default = None, description = "기간 한정 행사의 시작일. 아니면 null")
    event_end_date : Optional[date] = Field(default = None, description = "기간 한정 행사의 종료일. 아니면 null")
    event_period_unresolved : bool = Field(description = "기간 한정 행사로 보이는데 종료일을 끝내 못 찾았으면 true")


class VerifyPlaceLLM(BaseModel):
    exists : bool = Field(description = "검색으로 실제 운영 중인 장소를 하나라도 찾았는지")
    candidates : list[GroundedPlace] = Field(description = "찾은 장소들. 지역·특징이 가장 잘 맞는 곳부터 순서대로")
