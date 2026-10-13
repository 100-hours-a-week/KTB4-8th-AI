from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.business_hours import BusinessHours
from app.schemas.category import PlaceCategory
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
# VideoAnalysisResult 로 옮겨진다. LLM 전용 필드(screen_text, video_index)를
# 떼어낸 모양이다.
class VideoAnalysis(BaseModel):
    place_name : Optional[str] = Field(default = None, description = "영상에 나온 장소 이름")
    region : Optional[str] = Field(default = None, description = "영상에 나온 장소 위치")
    location_detail : Optional[str] = Field(default = None, description = "영상에 나온 건물명·층·가까운 역 출구")
    category : PlaceCategory = Field(description = "카페, 팝업, 전시, 맛집, 명소 어디에도 해당하지 않으면 기타로 분류한다")
    event_start_date : Optional[date] = Field(default = None, description = "행사 개최일, 팝업/전시 카테고리에서만 값을 채움")
    event_end_date : Optional[date] = Field(default = None, description = "행사 마감일, 팝업/전시 카테고리에서만 값을 채움")
    confidence : float = Field(description = "추출된 장소에 대한 신뢰도", ge = 0.0, le = 1.0)
    summary : Optional[str] = Field(default = None, description = "장소 요약")


# LLM 전용 스키마. 응답으로 나가지 않는다 — 서비스가 to_data() 로 옮기며 버린다.
# 한 항목 = 장소 하나다. 영상에 장소가 여러 곳이면 항목도 여러 개다.
#
# screen_text 가 맨 앞에 있는 것이 이 모델의 존재 이유다. 필드 순서가 곧
# 모델의 작업 순서라, 화면 글자를 먼저 적게 하면 그 과정에서 로고·현수막을
# 실제로 읽고 place_name 이 거기서 나온다. 이 칸이 없으면 읽는 단계 자체가
# 생략되어 place_name 이 계속 비어서 나왔다. 장소가 여러 곳일 때 영상 글자
# 전부를 항목마다 반복하면 출력이 장소 수만큼 불어나므로, 그 장소의 근거가
# 된 글자만 적게 한다.
class AnalyzeVideoLLM(BaseModel):
    screen_text : list[str] = Field(
        description = "이 장소의 근거가 된 화면 글자. 자막뿐 아니라 간판·현수막·"
                      "메뉴판·포장지·차량 표기·벽면 문구, 티셔츠·모자·앞치마의 로고까지. "
                      "각 항목 앞에 몇 초 지점인지 적는다. 예: '00:03 모자 로고 - 청년방앗간'. "
                      "장소를 찾지 못한 영상이면 읽은 글자를 전부 적는다"
    )
    place_name : Optional[str] = Field(default = None, description = "이 항목이 가리키는 장소 이름. screen_text 에서 찾는다")
    region : Optional[str] = Field(default = None, description = "영상에 나온 장소 위치")
    location_detail : Optional[str] = Field(default = None, description = "영상에 나온 건물명·층·가까운 역 출구. 영상에 없으면 null")
    category : PlaceCategory = Field(description = "카페, 팝업, 전시, 맛집, 명소 어디에도 해당하지 않으면 기타로 분류한다")
    event_start_date : Optional[date] = Field(default = None, description = "장소 개최일, 팝업/전시 카테고리에서만 값을 채움")
    event_end_date : Optional[date] = Field(default = None, description = "장소 마감일, 팝업/전시 카테고리에서만 값을 채움")
    confidence : float = Field(description = "추출된 장소에 대한 신뢰도", ge = 0.0, le = 1.0)
    summary : Optional[str] = Field(default = None, description = "장소 요약")


# LLM 전용. Gemini 호출 1번에 영상을 여러 개 넣으면 결과가 목록으로 온다.
class _Indexed(BaseModel):
    video_index : int = Field(description = "몇 번째 영상의 결과인지. 영상 앞에 붙은 번호를 그대로 적는다")


# 부모 순서를 (AnalyzeVideoLLM, _Indexed) 로 두면 필드가 video_index →
# screen_text → place_name … 순이 된다. 번호를 먼저 적게 해야 모델이 "지금
# 몇 번 영상을 쓰는 중인지" 붙잡고, 영상끼리 정보가 섞이는 걸 줄인다.
class AnalyzeVideoBatchItemLLM(AnalyzeVideoLLM, _Indexed):
    def to_data(self) -> VideoAnalysis:
        return VideoAnalysis(**self.model_dump(exclude={"screen_text", "video_index"}))


class AnalyzeVideoBatchLLM(BaseModel):
    results : list[AnalyzeVideoBatchItemLLM] = Field(description = "장소마다 하나씩, 영상 번호 순서대로. 장소가 없는 영상도 장소명을 비운 항목 하나")
