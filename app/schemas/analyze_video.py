from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.category import PlaceCategory
from app.schemas.common import APIResponse, ResponseMessage

#요청 스키마
class AnalyzeVideoRequest(BaseModel):
    video_url : str = Field(description = "유튜브 쇼츠 영상 url")

#응답 스키마
class AnalyzedPlace(BaseModel):
    place_name : Optional[str] = Field(default = None, description = "장소의 고유 상호. 읽을 수 없으면 null. '~맛집들'·지역명 같은 묶음 표현은 상호가 아니다")
    region : Optional[str] = Field(default = None, description = "장소 위치")
    category : PlaceCategory = Field(description = "카페, 팝업, 전시, 맛집, 명소 어디에도 해당하지 않으면 기타로 분류한다")
    event_start_date : Optional[date] = Field(default = None, description = "행사 개최일, 팝업/전시 카테고리에서만 값을 채움")
    event_end_date : Optional[date] = Field(default = None, description = "행사 마감일, 팝업/전시 카테고리에서만 값을 채움")
    confidence : float = Field(description = "추출된 장소에 대한 신뢰도", ge = 0.0, le = 1.0)
    summary : Optional[str] = Field(default = None, description = "장소 요약")


class AnalyzeVideoData(BaseModel):
    # 한 영상에 여러 곳이 나오는 모음 영상이 흔해서 목록으로 받는다 — 테스트 영상
    # 11개 중 4개가 6~16곳짜리였다. 하나만 받게 했을 땐 모델이 매번 아무거나
    # 골라 결과가 회차마다 바뀌었고 나머지는 버려졌다.
    # 이름을 못 읽은 장소도 넣는다 — 지역·요약이 있어 나중에 특징 검색으로
    # 찾을 여지가 있고, 버리면 되돌릴 수 없다.
    places : list[AnalyzedPlace] = Field(description = "영상에 나온 방문 가능한 장소 전부, 등장 순서대로. 없으면 빈 목록")

AnalyzeVideoResponse = APIResponse[AnalyzeVideoData]


# ── LLM 전용 스키마. 응답으로 나가지 않는다 — to_data() 로 옮기며 근거 칸은 버린다.

# 장소마다 근거 칸. "몇 초 지점, 무엇에서 읽었는지"를 쓰게 하니 모음 영상에서
# 6~16곳을 빠짐없이 뽑았다(실측). 부모 순서를 (AnalyzedPlace, _PlaceEvidence)
# 로 두면 이 두 칸이 place_name 보다 앞에 온다 — 근거를 먼저 적어야 실제로 본다.
class _PlaceEvidence(BaseModel):
    time_range : str = Field(description = "영상에서 이 장소가 나오는 구간. 예: '00:03~00:08'")
    name_source : str = Field(description = "상호를 무엇에서 읽었는지 — 간판·로고·현수막·자막·음성. 못 읽었으면 '없음'")


class PlaceLLM(AnalyzedPlace, _PlaceEvidence):
    def to_place(self) -> AnalyzedPlace:
        return AnalyzedPlace(**self.model_dump(exclude={"time_range", "name_source"}))


# screen_text 가 맨 앞에 있는 이유: 필드 순서가 곧 모델의 작업 순서라, 화면
# 글자를 먼저 적게 하면 그 과정에서 로고·현수막을 실제로 읽는다. 이 칸이 없을
# 땐 읽는 단계 자체가 생략되어 place_name 이 계속 비어서 나왔다.
class AnalyzeVideoLLM(BaseModel):
    screen_text : list[str] = Field(
        description = "영상 화면에서 읽은 글자를 전부. 자막뿐 아니라 간판·현수막·"
                      "메뉴판·포장지·차량 표기·벽면 문구, 티셔츠·모자·앞치마의 로고까지. "
                      "각 항목 앞에 몇 초 지점인지 적는다. 예: '00:03 모자 로고 - 청년방앗간'"
    )
    places : list[PlaceLLM] = Field(description = "영상에 나온 방문 가능한 장소 전부, 등장 순서대로. 없으면 빈 목록")

    def to_data(self) -> AnalyzeVideoData:
        return AnalyzeVideoData(places=[p.to_place() for p in self.places])

# ── 배치 분석 (/v1/analyze-videos) ────────────────────────────────────

#요청 스키마
class AnalyzeVideosRequest(BaseModel):
    video_urls : list[str] = Field(min_length = 1, max_length = 100, description = "유튜브 쇼츠 영상 url 목록")


#응답 스키마
class VideoAnalysisResult(BaseModel):
    video_url : str = Field(description = "요청에 들어온 url 그대로")
    status : Literal["success", "failed"] = Field(description = "이 영상의 분석 성공 여부")
    result : Optional[AnalyzeVideoData] = Field(default = None, description = "성공 시 분석 결과")
    error : Optional[ResponseMessage] = Field(default = None, description = "실패 시 원인 코드 (llm_timeout 등)")


class AnalyzeVideosData(BaseModel):
    # 입력 순서와 같다. 백엔드는 인덱스로 요청 url 과 짝지을 수 있다.
    results : list[VideoAnalysisResult] = Field(description = "영상별 결과. 요청 순서와 같음")


AnalyzeVideosResponse = APIResponse[AnalyzeVideosData]


# LLM 전용. Gemini 호출 1번에 영상을 여러 개 넣으면 결과가 목록으로 온다.
class _Indexed(BaseModel):
    video_index : int = Field(description = "몇 번째 영상의 결과인지. 영상 앞에 붙은 번호를 그대로 적는다")


# 부모 순서를 (AnalyzeVideoLLM, _Indexed) 로 두면 필드가 video_index →
# screen_text → places 순이 된다. 번호를 먼저 적게 해야 모델이 "지금 몇 번
# 영상을 쓰는 중인지" 붙잡고, 영상끼리 정보가 섞이는 걸 줄인다.
# to_data() 는 places 만 옮기므로 부모 것을 그대로 쓴다.
class AnalyzeVideoBatchItemLLM(AnalyzeVideoLLM, _Indexed):
    pass


class AnalyzeVideoBatchLLM(BaseModel):
    results : list[AnalyzeVideoBatchItemLLM] = Field(description = "영상마다 하나씩, 번호 순서대로")
