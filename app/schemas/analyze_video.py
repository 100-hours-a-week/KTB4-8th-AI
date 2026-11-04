from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.business_hours import BusinessHours
from app.schemas.category import PlaceCategory, PlaceType
from app.schemas.common import APIResponse, ResponseMessage

#요청 스키마
class AnalyzeVideosRequest(BaseModel):
    video_urls : list[str] = Field(min_length = 1, max_length = 100, description = "유튜브 쇼츠 영상 url 목록")


#응답 스키마
VideoStatus = Literal["success", "not_found", "failed"]


# 한 항목 = 장소 하나. 영상에 장소가 여러 곳이면 같은 video_url 로 여러 항목이
# 나간다. 시트: 실패나 not_found 여도 모든 키가 존재하고 값만 null 이다.
# pydantic 은 None 도 키로 내보내므로 따로 할 일은 없다 — 라우트에
# response_model_exclude_none 을 걸면 이 약속이 깨진다.
class VideoAnalysisResult(BaseModel):
    video_url : str = Field(description = "요청에 들어온 url 그대로. 같은 영상의 장소끼리 묶는 키")
    status : VideoStatus = Field(description = "success: 장소 확인 / not_found: 이 영상에서 확인한 장소가 없음(영상당 한 항목) / failed: 처리 오류, 영상 전체를 다시 요청(영상당 한 항목)")
    error : Optional[ResponseMessage] = Field(default = None, description = "failed 일 때 원인 코드 (llm_timeout 등)")
    place_name : Optional[str] = Field(default = None, description = "검증된 장소명")
    address : Optional[str] = Field(default = None, description = "도로명 주소")
    location_detail : Optional[str] = Field(default = None, description = "주소에 없는 위치 정보 (건물명·층·가까운 역 출구). 검색 결과 우선, 없으면 영상. 둘 다 없으면 null")
    category : Optional[PlaceCategory] = Field(default = None, description = "장소 카테고리")
    business_hours : Optional[BusinessHours] = Field(default = None, description = "영업시간. success 항목에는 항상 있다(못 찾은 장소는 not_found). not_found·failed 는 null")
    event_start_date : Optional[date] = Field(default = None, description = "행사 시작일")
    event_end_date : Optional[date] = Field(default = None, description = "행사 종료일")
    event_period_unresolved : Optional[bool] = Field(default = None, description = "기간 한정 행사인데 종료일을 못 찾았는지")
    place_type : Optional[PlaceType] = Field(default = None, description = "카테고리가 팝업·전시·공연·축제면 이벤트, 나머지는 장소. not_found·failed 는 null")
    lat : Optional[float] = Field(default = None, description = "위도")
    lng : Optional[float] = Field(default = None, description = "경도")
    confidence : Optional[float] = Field(default = None, ge = 0.0, le = 1.0, description = "분석 신뢰도")
    # 위치를 넣지 않는다 — summary 는 embed-places 가 임베딩해 코스 추천 검색과
    # 취향 벡터(저장 장소 평균)에 쓴다. 위치가 섞이면 "어느 동네를 저장했나"가
    # 취향에 섞인다. 위치는 location_detail 로 따로 낸다.
    summary : Optional[str] = Field(default = None, description = "장소 특징 요약 (검색 조건이 될 속성. 위치는 location_detail)")


class AnalyzeVideosData(BaseModel):
    # 영상 순서는 요청과 같고, 같은 영상의 장소는 연속으로 나온다. 영상 하나가
    # 여러 항목이 될 수 있어 인덱스로는 요청 url 과 짝지을 수 없다 — video_url 로 묶는다.
    results : list[VideoAnalysisResult] = Field(description = "장소별 결과. 영상은 요청 순서대로, 같은 영상의 장소는 연속")


AnalyzeVideosResponse = APIResponse[AnalyzeVideosData]


# 분석 단계의 중간 결과. 응답으로 바로 나가지 않는다 — 장소 검증을 거쳐
# VideoAnalysisResult 로 옮겨진다. Gemma 추출(PlaceExtractionLLM)의 한 항목이기도 하다.
# 카테고리는 없다 — Jev 가 따로 고른다(11/3).
class VideoAnalysis(BaseModel):
    place_name : Optional[str] = Field(default = None, description = "영상에 나온 장소 이름")
    region : Optional[str] = Field(default = None, description = "영상에 나온 장소 위치")
    location_detail : Optional[str] = Field(default = None, description = "영상에 나온 건물명·층·가까운 역 출구")
    event_start_date : Optional[date] = Field(default = None, description = "행사 시작일. 기간이 정해진 행사(팝업·전시·공연·축제)일 때만 채움")
    event_end_date : Optional[date] = Field(default = None, description = "행사 종료일. 기간이 정해진 행사(팝업·전시·공연·축제)일 때만 채움")
    confidence : float = Field(description = "추출된 장소에 대한 신뢰도", ge = 0.0, le = 1.0)
    summary : Optional[str] = Field(default = None, description = "장소 요약")


# Gemini 관찰 단계(LLM 전용). 영상마다 한 항목. 필드 순서가 곧 작업 순서다 —
# 번호 → 형식 → 판정 → 업로드 날짜 → 관찰 기록 순으로 적게 해, 장소 영상만 기록을 쓰게 한다.
# 번호를 먼저 적게 해야 모델이 "지금 몇 번 영상을 쓰는 중인지" 붙잡고, 영상끼리 정보가 섞이는 걸 줄인다.
class VideoObservationLLM(BaseModel):
    video_index : int = Field(description = "몇 번째 영상인지. 영상 앞에 붙은 번호를 그대로 적는다")
    is_vertical : bool = Field(description = "세로 화면(쇼츠)이면 true")
    is_longform : bool = Field(description = "영상이 3분을 넘어 계속되면 true")
    verdict : Literal["장소", "해외", "롱폼", "장소 아님"] = Field(description = "영상 판정")
    upload_date : Optional[date] = Field(default = None, description = "영상 업로드 날짜. 확인할 수 없으면 null")
    observations : list[str] = Field(description = '"MM:SS 출처 - 내용" 한 줄씩. 장소 영상이 아니면 빈 목록')


class VideoObservationBatchLLM(BaseModel):
    results : list[VideoObservationLLM] = Field(description = "영상마다 하나씩, 영상 번호 순서대로")


# Gemma 추출 단계(LLM 전용). 영상 하나에서 뽑은 장소 목록.
class PlaceExtractionLLM(BaseModel):
    places : list[VideoAnalysis] = Field(description = "장소마다 하나. 장소를 특정하지 못하면 장소명을 비운 항목 하나")
