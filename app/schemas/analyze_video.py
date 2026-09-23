from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.common import APIResponse

#요청 스키마
class AnalyzeVideoRequest(BaseModel):
    video_url : str = Field(description = "유튜브 쇼츠 영상 url")

#응답 스키마
class AnalyzeVideoData(BaseModel):
    place_name : Optional[str] = Field(default = None, description = "영상에 나온 장소 이름")
    region : Optional[str] = Field(default = None, description = "영상에 나온 장소 위치")
    category : Literal["카페","팝업","전시","맛집","명소", "기타"] = Field(description = "카페, 팝업, 전시, 맛집, 명소 어디에도 해당하지 않으면 기타로 분류한다")
    event_start_date : Optional[date] = Field(default = None, description = "장소 개최일, 팝업/전시 카테고리에서만 값을 채움")
    event_end_date : Optional[date] = Field(default = None, description = "장소 마감일, 팝업/전시 카테고리에서만 값을 채움")
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
    category : Literal["카페","팝업","전시","맛집","명소", "기타"] = Field(description = "카페, 팝업, 전시, 맛집, 명소 어디에도 해당하지 않으면 기타로 분류한다")
    event_start_date : Optional[date] = Field(default = None, description = "장소 개최일, 팝업/전시 카테고리에서만 값을 채움")
    event_end_date : Optional[date] = Field(default = None, description = "장소 마감일, 팝업/전시 카테고리에서만 값을 채움")
    confidence : float = Field(description = "추출된 장소에 대한 신뢰도", ge = 0.0, le = 1.0)
    summary : Optional[str] = Field(default = None, description = "장소 요약")

    def to_data(self) -> AnalyzeVideoData:
        return AnalyzeVideoData(**self.model_dump(exclude={"screen_text"}))