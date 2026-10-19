from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.business_hours import BusinessHours
from app.schemas.category import PlaceCategory
from app.schemas.common import APIResponse, ResponseMessage
from app.schemas.extract import AvailableTime, VisitDatetime


#요청 스키마
class Coordinate(BaseModel):
    """좌표"""
    lat: float = Field(..., description="위도")
    lng: float = Field(..., description="경도")


class RecommendCandidate(BaseModel):
    """추천 후보 장소 모델"""
    place_id: str = Field(..., description="장소 식별자")
    place_name: str = Field(..., description="장소명")
    summary: str = Field(..., description="장소 특징 요약")
    lat: float = Field(..., description="위도")
    lng: float = Field(..., description="경도")
    # analyze-video 응답의 business_hours 를 그대로 받는다. null 불가 — success 로
    # 나온 장소는 영업시간이 항상 있다.
    business_hours: BusinessHours = Field(..., description="영업시간 (7개 요일, 예외일, 추정 여부)")


class HistoryPlace(BaseModel):
    """과거 저장 장소 (취향 벡터 계산용)"""
    place_id: str = Field(..., description="장소 식별자")
    saved_at: date = Field(..., description="저장 일자")


# API 시트 기준: 요청 body에 request_id 없음
class RecommendCoursesRequest(BaseModel):
    """코스 추천 요청 모델"""
    query: str = Field(..., description="정성 조건")
    candidates: list[RecommendCandidate] = Field(..., max_length=50, description="백엔드가 좌표 반경으로 1차 필터링한 후보 목록 (최대 50개)")
    history_place_ids: list[HistoryPlace] = Field(default_factory=list, max_length=50, description="최근 저장한 장소 목록, 취향 벡터 계산용 (최대 50개). 비어있으면 query만으로 검색")
    available_time: Optional[AvailableTime] = Field(default=None, description="외출 가능 시간(분) (180·360·540)")
    category: Optional[list[PlaceCategory]] = Field(default=None, description="카테고리 목록 (extract 의 slot.category 그대로)")
    datetime: VisitDatetime = None
    origin: Optional[Coordinate] = Field(default=None, description="출발지 좌표 (첫 장소까지의 이동 시간 계산 기준)")


#응답 스키마
class CoursePlace(BaseModel):
    """코스에 포함된 장소. 리스트 순서가 곧 방문 순서"""
    place_id: str = Field(..., description="장소 식별자")
    place_name: str = Field(..., description="장소명")
    travel_minutes: int = Field(..., description="직전 지점에서의 이동 시간(분)")
    arrival_time: str = Field(..., description="도착 예정 시각 (HH:MM)")
    stay_minutes: int = Field(..., description="예상 체류 시간(분)")
    reason: str = Field(..., description="이 장소가 선정된 근거")


class Course(BaseModel):
    """코스 모델"""
    title: str = Field(..., description="코스 제목 (LLM 생성)")
    # 확률이 아니다 — 유사도를 고정 구간으로 환산한 점수. 이름을 rate 가 아니라
    # score 로 둔 것도 그래서다. 계산할 수 없으면 0 이 아니라 null: 0 은 "안
    # 맞음"이라는 주장이라, 임베딩이 없어서 모르는 경우와 구분되지 않는다.
    match_score: Optional[int] = Field(
        default=None, ge=0, le=100,
        description="코스 추천도(0~100). 요청 조건+취향과의 유사도. 계산할 수 없으면 null",
    )
    places: list[CoursePlace] = Field(..., description="장소 목록. 방문 순서대로 정렬됨")
    total_duration_minutes: int = Field(..., description="이동+체류를 합한 총 소요 시간(분)")


class RecommendCoursesData(BaseModel):
    """코스 추천 결과 모델"""
    # 시트: 성공·없음·중단·실패 모두 200 이고, 실패 원인은 여기에 담는다.
    error: Optional[ResponseMessage] = Field(default=None, description="recommend_failed 일 때 원인 코드")
    courses: list[Course] = Field(default_factory=list, max_length=3, description="조건을 만족하는 코스 목록 (최대 3개)")


RecommendCoursesResponse = APIResponse[RecommendCoursesData]


# LLM 전용. 이동시간·도착 시각·총 소요 시간은 모델이 아니라 코드가 채운다 —
# 모델이 적은 분 단위 숫자는 그럴듯하게 지어낸 값이라 믿을 수 없다.
class RerankLLM(BaseModel):
    place_ids: list[str] = Field(description="조건에 맞고 방문 시각에 영업 중인 장소의 place_id. 적합도 내림차순")


class DraftPlace(BaseModel):
    place_id: str = Field(description="장소 식별자 (후보 목록에 있는 값만)")
    stay_minutes: int = Field(description="예상 체류 시간(분)")
    reason: str = Field(description="이 장소가 선정된 근거 (한 문장)")


class DraftCourse(BaseModel):
    title: str = Field(description="코스 제목")
    places: list[DraftPlace] = Field(description="방문할 장소 목록")


class ComposeLLM(BaseModel):
    courses: list[DraftCourse] = Field(description="서로 다른 장소 조합의 코스 (최대 3개)")
