from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.category import PlaceCategory
from app.schemas.common import APIResponse, ResponseMessage

#요청 스키마
class AnalyzeVideoRequest(BaseModel):
    video_url : str = Field(description = "유튜브 쇼츠 영상 url")

#응답 스키마
class AnalyzeVideoData(BaseModel):
    place_name : Optional[str] = Field(default = None, description = "영상에 나온 장소 이름")
    region : Optional[str] = Field(default = None, description = "영상에 나온 장소 위치")
    category : PlaceCategory = Field(description = "카페, 팝업, 전시, 맛집, 명소 어디에도 해당하지 않으면 기타로 분류한다")
    event_start_date : Optional[date] = Field(default = None, description = "행사 개최일, 팝업/전시 카테고리에서만 값을 채움")
    event_end_date : Optional[date] = Field(default = None, description = "행사 마감일, 팝업/전시 카테고리에서만 값을 채움")
    confidence : float = Field(description = "추출된 장소에 대한 신뢰도", ge = 0.0, le = 1.0)
    summary : Optional[str] = Field(default = None, description = "장소 요약")

AnalyzeVideoResponse = APIResponse[AnalyzeVideoData]


# LLM 전용 스키마. 응답으로 나가지 않는다 — 서비스가 to_data() 로 옮기며 버린다.
#
# screen_text 가 맨 앞에 있는 것이 이 모델의 존재 이유다. 필드 순서가 곧
# 모델의 작업 순서라, 화면 글자를 먼저 적게 하면 그 과정에서 로고·현수막을
# 실제로 읽고 place_name 이 거기서 나온다. 이 칸이 없으면 읽는 단계 자체가
# 생략되어 place_name 이 계속 비어서 나왔다.
class AnalyzeVideoLLM(BaseModel):
    screen_text : list[str] = Field(
        description = "영상 화면에서 읽은 글자를 전부. 자막뿐 아니라 간판·현수막·"
                      "메뉴판·포장지·차량 표기·벽면 문구, 티셔츠·모자·앞치마의 로고까지. "
                      "각 항목 앞에 몇 초 지점인지 적는다. 예: '00:03 모자 로고 - 청년방앗간'"
    )
    place_name : Optional[str] = Field(default = None, description = "영상에 나온 장소 이름. screen_text 에서 찾는다")
    region : Optional[str] = Field(default = None, description = "영상에 나온 장소 위치")
    category : PlaceCategory = Field(description = "카페, 팝업, 전시, 맛집, 명소 어디에도 해당하지 않으면 기타로 분류한다")
    event_start_date : Optional[date] = Field(default = None, description = "장소 개최일, 팝업/전시 카테고리에서만 값을 채움")
    event_end_date : Optional[date] = Field(default = None, description = "장소 마감일, 팝업/전시 카테고리에서만 값을 채움")
    confidence : float = Field(description = "추출된 장소에 대한 신뢰도", ge = 0.0, le = 1.0)
    summary : Optional[str] = Field(default = None, description = "장소 요약")

    def to_data(self) -> AnalyzeVideoData:
        return AnalyzeVideoData(**self.model_dump(exclude={"screen_text"}))

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
# screen_text → place_name … 순이 된다. 번호를 먼저 적게 해야 모델이 "지금
# 몇 번 영상을 쓰는 중인지" 붙잡고, 영상끼리 정보가 섞이는 걸 줄인다.
class AnalyzeVideoBatchItemLLM(AnalyzeVideoLLM, _Indexed):
    def to_data(self) -> AnalyzeVideoData:
        return AnalyzeVideoData(**self.model_dump(exclude={"screen_text", "video_index"}))


class AnalyzeVideoBatchLLM(BaseModel):
    results : list[AnalyzeVideoBatchItemLLM] = Field(description = "영상마다 하나씩, 번호 순서대로")
