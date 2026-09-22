작성일 : 2026년 9월 1일

작성자 : hayes.yu, amy.kim

> [!IMPORTANT]
> 2026/09/01 최초 작성<br>
> 2026/09/05 수정 — 재순위·코스 조합을 코스 추천 하나로 통합, 장소 임베딩 저장 추가, 오류 코드 세분화<br>
> 2026/09/07 수정 — 헬스체크 엔드포인트 안내 추가, 장소 검증 동명 후보 처리를 사용자 선택 요청에서 LLM 자동 선택(신뢰도순 정렬, candidates 목록 유지)으로 변경, 코스 추천 최대 3개 제한 명시, 행사 기간을 원문 문자열에서 시작일·종료일(analyze-video 추출 → verify-place 웹검색 보완)로 구조화<br>
> 2026/09/08 수정 — 벡터 DB를 Pinecone에서 Chroma(임베디드)로 변경, `preference`를 슬롯 추출·코스 추천에서 모두 제거하고 코스 추천에 `history_place_ids`(최근 저장 장소 최대 50개, 취향 벡터 계산용) 추가<br>
> 2026/09/22 수정 — 공통 카테고리 정의(2-2) 섹션 추가(카테고리 6종·구분 기준·정규화 기준, 기타 포함), 코스 추천 중단 엔드포인트 및 장소 임베딩 삭제 엔드포인트 반영, 영상 분석·슬롯 추출·코스 추천이 `PlaceCategory` 하나를 공유하도록 통일, `available_time`을 "3시간" 등 라벨에서 분을 나타내는 문자열(`"180"`·`"360"`·`"540"`)로 변경 (Gemini 구조화된 출력의 `enum` 제약이 숫자 타입을 지원하지 않아 숫자가 아닌 문자열로 확정)
>
> 본 문서는 「KeepGo」의 API 설계서 입니다.

### 목차

---

1. [API 엔드포인트 목록](#1-api-엔드포인트-목록)
2. [API 입출력 명세](#2-api-입출력-명세)
3. [API 역할 및 연동 관계](#3-api-역할-및-연동-관계)
4. [API 요청 및 응답 예시](#4-api-요청-및-응답-예시)

---

### 1. API 엔드포인트 목록

| HTTP Method | 엔드포인트 | 기능 설명 |
| --- | --- | --- |
| POST | /v1/analyze-video | 유튜브 쇼츠 영상 분석 (장소명·지역·카테고리·행사 시작일·종료일 추출) |
| POST | /v1/verify-place | 장소 존재 여부·영업시간 검증 및 좌표 조회 |
| POST | /v1/extract | 사용자 발화에서 슬롯·정성 조건 추출 |
| POST | /v1/recommend-courses | 후보 장소로 코스 조합·방문 순서·도착 시각 생성 |
| POST | /v1/recommend-courses/{request_id}/cancel | 진행 중인 코스 추천 요청 중단 |
| POST | /v1/embed-places | 장소 요약을 임베딩해 벡터 DB에 저장 |

> [!NOTE]
> 운영용으로 `GET /health`를 별도 제공합니다.

---

### 2. API 입출력 명세

#### 2-1) 공통 응답 래퍼

모든 엔드포인트는 `message`와 `data` 두 필드로 구성된 동일한 응답 형태를 사용합니다. 실패 시 `data`는 항상 `null`입니다.

<details>
<summary>Pydantic 모델 상세보기</summary>

<pre><code class="language-python">
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

class APIResponse(BaseModel, Generic[T]):
    """모든 엔드포인트가 공유하는 응답 envelope"""
    message: str = Field(..., description="처리 결과 코드")
    data: Optional[T] = Field(default=None, description="응답 데이터 (실패 시 null)")
</code></pre>

</details>

---

#### 2-2) 공통 카테고리 정의

`analyze-video`, `extract` 등 카테고리를 다루는 모든 엔드포인트는 아래 `PlaceCategory` 하나를 공유해서 사용합니다. 엔드포인트마다 카테고리 값이 달라지는 것을 막기 위해 별도 모듈(`app/schemas/category.py`)로 분리했습니다.

| 카테고리 | 설명 | 예시 |
| --- | --- | --- |
| 카페 | 커피·디저트 등을 파는 상업 공간 | 성수 A카페 |
| 팝업 | 한시적으로 운영되는 팝업스토어 | OO 브랜드 팝업 |
| 전시 | 전시회·갤러리 | OO 사진전 |
| 맛집 | 식사를 파는 상업 공간 | 농민뜨끈이 |
| 명소 | 특정 업장이 아니라 장소·동네 자체가 컨텐츠가 되는 곳 | 한강, 경복궁, 행궁동 |
| 기타 | 위 5개 어디에도 해당하지 않는 경우 | - |

> [!NOTE]
> 구분 기준: 특정 업장 방문이 핵심이면 카페/팝업/전시/맛집 중 하나, 장소·동네를 둘러보는 것 자체가 핵심이면 명소로 분류합니다. (예: "성수동 카페 방문" 브이로그는 카페, "성수동 골목 구경" 브이로그는 명소) 이 중 어디에도 해당하지 않으면 기타로 분류합니다.

<details>
<summary>Pydantic 모델 상세보기</summary>

<pre><code class="language-python">
from typing import Literal

PlaceCategory = Literal["카페", "팝업", "전시", "맛집", "명소", "기타"]
</code></pre>

</details>

**정규화 기준**

동의어·표기 차이(예: "커피숍" → 카페)에 대한 별도 매핑 테이블은 두지 않습니다. `extract`, `analyze-video`가 LLM을 호출할 때 이 `PlaceCategory`를 포함한 응답 스키마를 `response_schema`로 넘겨 구조화된 출력을 강제하면, 모델이 생성 단계에서부터 이 6개 값 중 하나만 출력하도록 제한되기 때문에 변형 표현이 애초에 출력될 수 없습니다.

---

#### 2-3) 영상 분석

유튜브 쇼츠 URL을 받아 장소 정보를 구조화해 반환합니다.

> 요청 데이터 : 영상 URL

> 응답 데이터 : 장소명·지역·카테고리·행사 시작일·종료일·신뢰도·요약

> [!NOTE]
> 영상에서 확인되지 않은 항목은 임의로 채우지 않고 `null`로 반환합니다. `category`(애매하면 "기타")와 `confidence`는 항상 값이 있습니다.

> [!NOTE]
> `event_start_date`·`event_end_date`는 `category`가 팝업·전시일 때만 채웁니다. 영상에 명확히 나오지 않으면 `null`로 두고, `verify-place`가 웹검색으로 보완합니다.

<details>
<summary>Pydantic 모델 상세보기</summary>

<pre><code class="language-python">
from app.schemas.category import PlaceCategory

class AnalyzeVideoRequest(BaseModel):
    """영상 분석 요청 모델"""
    video_url: str = Field(..., description="유튜브 쇼츠 영상 URL")


class AnalyzeVideoData(BaseModel):
    """영상 분석 결과 모델. 영상에서 확인되지 않은 항목은 null"""
    place_name: Optional[str] = Field(default=None, description="영상에서 확인된 장소명")
    region: Optional[str] = Field(default=None, description="영상에서 확인된 지역")
    category: PlaceCategory = Field(..., description="카테고리 (카페·팝업·전시·맛집·명소·기타)")
    event_start_date: Optional[date] = Field(default=None, description="행사 시작일 (팝업·전시에서 영상에 명확히 나온 경우만)")
    event_end_date: Optional[date] = Field(default=None, description="행사 종료일 (팝업·전시에서 영상에 명확히 나온 경우만)")
    confidence: float = Field(..., description="추출 결과에 대한 신뢰도")
    summary: Optional[str] = Field(default=None, description="장소 특징 요약, 자연어 1~2문장")


AnalyzeVideoResponse = APIResponse[AnalyzeVideoData]
</code></pre>

</details>

---

#### 2-4) 장소 검증

분석된 장소명·지역으로 실제 존재 여부와 영업시간, 좌표를 확인합니다.

> 요청 데이터 : 장소명(필수), 지역·요약(선택)

> 응답 데이터 : 존재 여부, 장소 후보 목록(장소명·주소·영업시간·좌표·행사 시작일·종료일)

> [!NOTE]
> 검증은 백그라운드 동기화 중 호출되어 사용자에게 선택을 요청할 수 없습니다. 동명 후보가 여러 곳 검색되면 `region`·`summary`를 근거로 LLM이 `candidates`를 신뢰도순으로 정렬해 반환하며, 1번째 원소를 자동 채택해 저장합니다. 나머지 후보는 자동 선택이 틀렸을 때 재선택하거나 추후 확인용으로 응답에 그대로 남깁니다. 검색 결과가 없으면 `exists: false` + 빈 목록입니다.

> [!NOTE]
> `event_start_date`·`event_end_date`가 요청에 비어 있고 `summary`상 기간 한정 행사로 보이면 웹검색으로 종료일을 찾아 채웁니다. 그래도 못 찾으면 `event_period_unresolved: true`로 표시해 사용자 확인이 필요함을 알립니다.

<details>
<summary>Pydantic 모델 상세보기</summary>

<pre><code class="language-python">
class VerifyPlaceRequest(BaseModel):
    """장소 검증 요청 모델"""
    place_name: str = Field(..., description="검증할 장소명")
    region: Optional[str] = Field(default=None, description="지역 (검색 정확도 보완용)")
    summary: Optional[str] = Field(default=None, description="장소 특징 요약 (검색 정확도 보완용)")
    event_start_date: Optional[date] = Field(default=None, description="analyze-video가 찾은 행사 시작일 (없으면 null)")
    event_end_date: Optional[date] = Field(default=None, description="analyze-video가 찾은 행사 종료일 (없으면 null)")


class PlaceCandidate(BaseModel):
    """검증된 장소 후보"""
    place_name: str = Field(..., description="검색된 장소명")
    address: str = Field(..., description="정규 주소")
    business_hours: str = Field(..., description="영업시간")
    lat: float = Field(..., description="위도")
    lng: float = Field(..., description="경도")
    event_start_date: Optional[date] = Field(default=None, description="확정된 행사 시작일 (기간 한정 행사가 아니면 null)")
    event_end_date: Optional[date] = Field(default=None, description="확정된 행사 종료일 (기간 한정 행사가 아니면 null)")
    event_period_unresolved: bool = Field(..., description="기간 한정 행사로 보이나 종료일을 끝내 확인하지 못해 사용자 확인이 필요한지 여부")


class VerifyPlaceData(BaseModel):
    """장소 검증 결과 모델. candidates는 신뢰도순 정렬, 1번째가 자동 채택값"""
    exists: bool = Field(..., description="장소 실존 여부")
    candidates: List[PlaceCandidate] = Field(..., description="검증된 장소 후보 목록 (신뢰도순, 1번째가 자동 채택값)")


VerifyPlaceResponse = APIResponse[VerifyPlaceData]
</code></pre>

</details>

---

#### 2-5) 슬롯 추출

사용자 발화에서 대화 슬롯과 정성 조건을 추출합니다.

> 요청 데이터 : 발화, 오늘 날짜, 이전 슬롯 상태(필수), 이전 정성 조건(선택)

> 응답 데이터 : 갱신된 슬롯, 정성 조건, 봇 메시지

> [!NOTE]
> 슬롯 내부 요소는 전부 String이며 `null` 값이 될 수 있습니다. `available_time`은 분 단위를 나타내는 문자열로, 정해진 세 값(`"180"`·`"360"`·`"540"`, 각각 3시간·6시간·9시간) 중 하나만 가질 수 있습니다. 숫자가 아니라 문자열인 이유는 Gemini의 구조화된 출력(`response_schema`)이 `enum` 제약에 숫자 타입을 지원하지 않기 때문입니다.

> [!NOTE]
> `query`는 이전 턴의 `query`와 이번 발화를 종합해 **현재 유효한 내용만** 재작성합니다(모순·철회된 표현은 제거). 슬롯 외 조건은 이 값 하나로 관리하며, 사용자의 과거 취향은 `recommend-courses`의 `history_place_ids`로 별도 반영합니다.

<details>
<summary>Pydantic 모델 상세보기</summary>

<pre><code class="language-python">
from app.schemas.available_time import AvailableTime  # Literal["180", "360", "540"]
from app.schemas.category import PlaceCategory

class Slots(BaseModel):
    """대화 슬롯 모델. 내부 요소는 전부 String이며 null이 될 수 있음"""
    origin: Optional[str] = Field(default=None, description="출발지")
    region: Optional[str] = Field(default=None, description="지역")
    datetime: Optional[str] = Field(default=None, description="날짜·시간대")
    available_time: Optional[AvailableTime] = Field(default=None, description="외출 가능 시간(분, 문자열) (\"180\"·\"360\"·\"540\")")
    category: Optional[PlaceCategory] = Field(default=None, description="카테고리")


class ExtractRequest(BaseModel):
    """슬롯 추출 요청 모델"""
    chat: str = Field(..., description="사용자 발화")
    date: date = Field(..., description="오늘 날짜 (상대 표현 변환 기준)")
    prev_slot: Slots = Field(..., description="이전 턴까지 채워진 슬롯 상태")
    prev_query: Optional[str] = Field(default=None, description="이전 턴까지의 정성 조건")


class ExtractData(BaseModel):
    """슬롯 추출 결과 모델"""
    slot: Slots = Field(..., description="누적 갱신된 슬롯 값")
    query: str = Field(..., description="정성 조건")
    bot_message: str = Field(..., description="사용자에게 보여줄 응답 문구")


ExtractResponse = APIResponse[ExtractData]
</code></pre>

</details>

---

#### 2-6) 코스 추천

정성 조건과 후보 목록을 받아, 코스 조합·제목·방문 순서·도착 시각까지 생성합니다.

> 요청 데이터 : 정성 조건·후보 목록(필수), 최근 저장 목록·외출 가능 시간·카테고리·방문 일시·출발지(선택)

> 응답 데이터 : 코스 목록(최대 3개, 제목 + 방문 순서대로 정렬된 장소 + 총 소요 시간)

> [!NOTE]
> 후보 압축 → 적합도 판단 → 코스 조합 → 이동 시간·도착 시각 계산 → 조건 검증까지 이 엔드포인트 안에서 처리합니다. 백엔드는 좌표 반경으로 1차 필터링한 후보만 넘기면 됩니다.

> [!NOTE]
> 서로 다른 코스를 최대 3개까지 구성합니다.

> [!NOTE]
> `history_place_ids`로 과거 취향을 고려하여 추천에 적용합니다. 비어있으면(신규 사용자 등) `query`만으로 검색합니다.

> [!IMPORTANT]
> **`available_time` 초과 여부는 코드로 검증하고, 영업시간·휴무일 판단은 LLM이 합니다.** `business_hours`가 자유 문자열이라 코드로 파싱할 수 없기 때문입니다. 원문과 `datetime`을 함께 프롬프트에 넣어 문 닫은 장소를 제외합니다.

> [!NOTE]
> `places`는 **방문 순서대로 정렬해서** 반환합니다. 별도의 순서 필드를 두지 않습니다.

<details>
<summary>Pydantic 모델 상세보기</summary>

<pre><code class="language-python">
from app.schemas.available_time import AvailableTime  # Literal["180", "360", "540"]
from app.schemas.category import PlaceCategory

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
    business_hours: str = Field(..., description="영업시간 (원문 문자열)")


class HistoryPlace(BaseModel):
    """과거 저장 장소 (취향 벡터 계산용)"""
    place_id: str = Field(..., description="장소 식별자")
    saved_at: date = Field(..., description="저장 일자")


class RecommendCoursesRequest(BaseModel):
    """코스 추천 요청 모델"""
    request_id: str = Field(..., description="이 추천 요청의 고유 식별자 (Backend가 생성, 중단 API에서 사용)")
    query: str = Field(..., description="정성 조건")
    candidates: List[RecommendCandidate] = Field(..., max_length=50, description="좌표 반경 1차 필터링을 통과한 후보 목록 (최대 50개)")
    history_place_ids: List[HistoryPlace] = Field(default_factory=list, max_length=50, description="최근 저장한 장소 목록, 취향 벡터 계산용 (최대 50개)")
    available_time: Optional[AvailableTime] = Field(default=None, description="외출 가능 시간(분, 문자열) (\"180\"·\"360\"·\"540\")")
    category: Optional[PlaceCategory] = Field(default=None, description="카테고리")
    datetime: Optional[str] = Field(default=None, description="방문 날짜·시간대 (영업시간 판단 기준)")
    origin: Optional[Coordinate] = Field(default=None, description="출발지 좌표 (첫 장소까지의 이동 시간 계산 기준)")


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
    places: List[CoursePlace] = Field(..., description="장소 목록. 방문 순서대로 정렬됨")
    total_duration_minutes: int = Field(..., description="이동+체류를 합한 총 소요 시간(분)")


class RecommendCoursesData(BaseModel):
    """코스 추천 결과 모델"""
    courses: List[Course] = Field(..., max_length=3, description="조건을 만족하는 코스 목록 (최대 3개)")


RecommendCoursesResponse = APIResponse[RecommendCoursesData]
</code></pre>

</details>

---

#### 2-7) 장소 임베딩 저장

보관함에 저장된 장소의 요약을 임베딩해 벡터 DB에 저장합니다.

> 요청 데이터 : 장소 목록 (식별자·장소명·요약)

> 응답 데이터 : 저장된 장소 수

> [!NOTE]
> 백엔드가 장소를 DB에 저장해 `place_id`가 확정된 뒤 호출합니다. 여러 건을 리스트로 받아 임베딩을 한 번에 처리하므로, 유튜브 동기화처럼 여러 장소를 연속 저장하는 경우 모아서 1회만 호출하면 됩니다.

> [!IMPORTANT]
> **동일한 `place_id`로 다시 호출하면 기존 임베딩을 덮어씁니다(upsert).** 사용자가 장소 정보를 수정한 경우 같은 엔드포인트를 다시 호출하면 되며, 별도의 갱신 엔드포인트는 두지 않습니다.
> 삭제 엔드포인트도 두지 않습니다. `recommend-courses`의 벡터 검색은 백엔드가 전달한 `candidates`의 `place_id` 범위 안에서만 수행되므로, 삭제된 장소의 벡터가 남아 있어도 추천 결과에 나타나지 않습니다.

<details>
<summary>Pydantic 모델 상세보기</summary>

<pre><code class="language-python">
class PlaceToEmbed(BaseModel):
    """임베딩 대상 장소 모델"""
    place_id: str = Field(..., description="장소 식별자 (백엔드 DB의 PK)")
    place_name: str = Field(..., description="장소명")
    summary: str = Field(..., description="장소 특징 요약")


class EmbedPlacesRequest(BaseModel):
    """장소 임베딩 저장 요청 모델"""
    places: List[PlaceToEmbed] = Field(..., description="임베딩할 장소 목록")


class EmbedPlacesData(BaseModel):
    """장소 임베딩 저장 결과 모델"""
    embedded_count: int = Field(..., description="저장에 성공한 장소 수")


EmbedPlacesResponse = APIResponse[EmbedPlacesData]
</code></pre>

</details>

---

### 3. API 역할 및 연동 관계

#### 3-1) 시스템 아키텍처

```
USER
  ↕
Frontend
  ↕
Backend
  ├─(콘텐츠 수집)→ AI Server
  │   ├─→ POST /v1/analyze-video      (영상 분석)
  │   ├─→ POST /v1/verify-place       (장소 검증, 분석 결과를 이어서 전달)
  │   └─→ POST /v1/embed-places       (임베딩 저장, MySQL 저장 후 호출)
  │
  ├─(챗봇 추천)→ AI Server
  │   ├─→ POST /v1/extract            (슬롯 추출, 대화 턴마다)
  │   └─→ POST /v1/recommend-courses  (코스 추천, 1차 필터링 결과를 전달)
  │
  └─→ MySQL       (보관함·지역 데이터 원본)
        ├─ 분석·검증 결과 저장
        ├─ 좌표 반경 기준 1차 필터링 조회 (recommend-courses 호출 전)
        └─ 법정동코드 기준 지역명 ↔ 좌표 변환

AI Server
  ├─ Chroma (임베디드, 프로세스 내)  (장소 요약 임베딩)
  │     ├─ 임베딩 저장 (embed-places)
  │     └─ 정성 조건 기반 후보 압축 (recommend-courses)
  └─→ 지도 API    (좌표·주소 조회, 이동 시간 계산)
```

> [!NOTE]
> Chroma에 들어가는 것은 **MySQL에서 파생된 검색 인덱스**이지 원본이 아닙니다. `place_id`와 `summary`를 임베딩한 값만 저장하며, 유실되어도 MySQL에서 다시 생성할 수 있습니다. 백엔드는 Chroma에 직접 접근하지 않습니다.

---

#### 3-2) 컴포넌트 역할 분리

**AI Server**
- 담당: 5개 엔드포인트 처리 (영상 분석, 장소 검증, 슬롯 추출, 코스 추천, 임베딩 저장). 코스 추천은 후보 압축·적합도 판단·코스 조합·이동 시간 계산·조건 검증을 내부에서 완결함
- 특징: 요청/응답이 이 문서에 정의된 스키마로 고정됨
- 상태 관리: LangGraph 그래프 내부 상태는 **요청 1건을 처리하는 동안에만** 유지되며 응답 후 소멸함. 요청 간 대화 이력은 서버가 보관하지 않고, 백엔드가 `prev_slot`·`prev_query`로 매 요청마다 전달함 (요청 단위 Stateless)
- LangGraph `checkpointer` 미사용: 챗봇 화면의 의도 카드는 사용자가 바텀시트로 직접 수정할 수 있어 슬롯 값의 소유권이 백엔드에 있어야 함. checkpointer로 AI 서버가 이력을 보관하면 수동 수정 내역을 서버 상태에 반영하는 별도 경로가 필요해지고, 상태 저장소(Redis 등) 인프라도 추가로 요구됨

**Backend**
- 담당: AI Server 호출, DB 저장·조회, 좌표 반경 1차 필터링, 지역명↔좌표 변환, SSE 스트리밍
- 코스 방문 순서·도착 시각 계산은 AI Server가 담당함 (추천 결과의 일부로 반환)
- 특징: AI Server가 반환한 결과를 바탕으로 실제 비즈니스 로직(필터링·정렬·저장)을 수행

**MySQL** (Backend 소유)
- 저장 데이터
  - 보관함 — 영상에서 분석·검증된 장소 정보(좌표 포함), 원본값과 사용자 수정값 구분 보관
  - 지역 — 법정동코드 기준 시도·시군구·읍면동·좌표
- 사용처: `analyze-video`·`verify-place` 결과 저장, `recommend-courses` 호출 전 좌표 반경 1차 필터링 조회, 챗봇에서 지역명 슬롯을 좌표로 변환(지도 API 대신 자체 DB 우선 조회)
- AI Server는 직접 접근하지 않습니다. 필요한 데이터는 전부 요청 본문으로 전달받습니다

**Chroma** (AI Server 프로세스에 임베디드로 내장, 별도 서버 아님)
- 저장 데이터: 장소 요약(`summary`)의 임베딩 벡터 + `place_id`
- 사용처: `embed-places`로 임베딩을 저장하고, `recommend-courses`에서 정성 조건과 유사한 후보만 남겨 LLM에 넘길 후보 수를 줄임
- MySQL에서 파생된 검색 인덱스이므로 원본이 아니며, 유실되어도 재생성할 수 있습니다

---

### 4. API 요청 및 응답 예시

- 엔드포인트 : POST /v1/analyze-video
기능 : 유튜브 쇼츠 URL을 분석해 장소 정보를 구조화 반환

요청 예시
```json
{
  "video_url": "https://youtube.com/shorts/xxxx"
}
```

응답 예시
```json
{
  "message": "analyze_success",
  "data": {
    "place_name": "농민뜨끈이",
    "region": null,
    "category": "맛집",
    "event_start_date": null,
    "event_end_date": null,
    "confidence": 0.95,
    "summary": "..."
  }
}
```

- 엔드포인트 : POST /v1/verify-place
기능 : 장소 존재 여부·영업시간 검증, 좌표 조회

요청 예시
```json
{
  "place_name": "메가커피",
  "region": null,
  "summary": "저가 아메리카노로 유명한 프랜차이즈 카페. 테이크아웃 위주고 좌석은 많지 않음.",
  "event_start_date": null,
  "event_end_date": null
}
```

응답 예시 (동명 후보 다수 — 신뢰도순 정렬, 1번째가 자동 채택값)
```json
{
  "message": "verify_success",
  "data": {
    "exists": true,
    "candidates": [
      {
        "place_name": "메가커피 성수점",
        "address": "서울특별시 성동구 성수동2가 1-1",
        "business_hours": "매일 08:00-22:00",
        "lat": 37.5441,
        "lng": 127.0560,
        "event_start_date": null,
        "event_end_date": null,
        "event_period_unresolved": false
      },
      {
        "place_name": "메가커피 건대점",
        "address": "서울특별시 광진구 화양동 2-2",
        "business_hours": "매일 09:00-23:00",
        "lat": 37.5401,
        "lng": 127.0694,
        "event_start_date": null,
        "event_end_date": null,
        "event_period_unresolved": false
      }
    ]
  }
}
```

> [!NOTE]
> 후보가 1건이면 `candidates`의 원소가 1개이고, 검색 결과가 없으면 `exists: false` + 빈 목록입니다.

- 엔드포인트 : POST /v1/extract
기능 : 사용자 발화에서 슬롯·정성 조건 추출

요청 예시
```json
{
  "chat": "조용한 카페 위주로 두세 시간 정도 볼 거야",
  "date": "2026-09-01",
  "prev_slot": {
    "origin": null,
    "region": "성수",
    "datetime": "2026-09-06",
    "available_time": null,
    "category": null
  },
  "prev_query": null
}
```

응답 예시
```json
{
  "message": "extract_success",
  "data": {
    "slot": {
      "origin": null,
      "region": "성수",
      "datetime": "2026-09-06",
      "available_time": "180",
      "category": "카페"
    },
    "query": "조용한",
    "bot_message": "성수에서 토요일에 조용한 카페 위주로 3시간 코스 찾아볼게요."
  }
}
```

- 엔드포인트 : POST /v1/recommend-courses
기능 : 후보 장소로 코스 조합·방문 순서·도착 시각 생성

요청 예시
```json
{
  "request_id": "req_c9f1a2",
  "query": "조용한",
  "candidates": [
    {
      "place_id": "p001",
      "place_name": "성수 A카페",
      "summary": "통유리에 좌석 간격 넓고 대화 소음 적음",
      "lat": 37.5445,
      "lng": 127.0557,
      "business_hours": "매일 10:00-22:00"
    },
    {
      "place_id": "p003",
      "place_name": "성수 books",
      "summary": "책 읽는 손님 위주라 대화 소음이 적음",
      "lat": 37.5462,
      "lng": 127.0538,
      "business_hours": "매일 11:00-21:00 (월요일 휴무)"
    }
  ],
  "history_place_ids": [
    { "place_id": "p010", "saved_at": "2026-08-20" },
    { "place_id": "p022", "saved_at": "2026-08-25" }
  ],
  "available_time": "180",
  "category": "카페",
  "datetime": "2026-09-06 14:00",
  "origin": { "lat": 37.5445, "lng": 127.0557 }
}
```

응답 예시
```json
{
  "message": "recommend_success",
  "data": {
    "courses": [
      {
        "title": "성수 조용한 카페 산책",
        "places": [
          {
            "place_id": "p001",
            "place_name": "성수 A카페",
            "travel_minutes": 0,
            "arrival_time": "14:00",
            "stay_minutes": 60,
            "reason": "좌석 간격이 넓고 소음이 적어 조용한 조건에 부합"
          },
          {
            "place_id": "p003",
            "place_name": "성수 books",
            "travel_minutes": 8,
            "arrival_time": "15:08",
            "stay_minutes": 70,
            "reason": "책 읽는 손님 위주라 오래 머물기 좋음"
          }
        ],
        "total_duration_minutes": 138
      }
    ]
  }
}
```

- 엔드포인트 : POST /v1/embed-places
기능 : 장소 요약을 임베딩해 벡터 DB에 저장

요청 예시
```json
{
  "places": [
    { "place_id": "p001", "place_name": "성수 A카페", "summary": "통유리에 좌석 간격 넓고 대화 소음 적음" },
    { "place_id": "p002", "place_name": "성수 B카페", "summary": "디저트 유명, 주말 웨이팅 길고 붐빔" }
  ]
}
```

응답 예시
```json
{
  "message": "embed_success",
  "data": {
    "embedded_count": 2
  }
}
```

---

### Status Code

| Status Code | Message | Description |
| --- | --- | --- |
| 200 | analyze_success / verify_success / extract_success / recommend_success / embed_success / cancel_accepted / embed_delete_success | 요청 성공 |
| 400 | invalid_request | 값이 부적절 |
| 422 | validation_error | 요청 스키마 위반 |
| 429 | llm_rate_limited | LLM 한도 초과 |
| 500 | internal_server_error | 서버 처리 중 오류 발생 |
| 502 | llm_invalid_response | LLM 응답 스키마 위반 |
| 502 | map_api_error | 지도 API 호출 실패 (verify-place, recommend-courses) |
| 504 | llm_timeout | LLM 호출 타임아웃 |

---

### 비고

- AI Server는 백엔드만 호출하는 내부 서비스입니다. 외부 노출을 막는 방식(네트워크 격리 / 공유 시크릿)은 인프라 구성 시점에 확정합니다.
- `business_hours`가 자유 문자열이라 영업시간 판단을 LLM이 담당합니다. `verify-place`가 영업시간을 구조화해 반환하도록 바꾸면 코드 검증으로 옮길 수 있습니다.
- `event_end_date`로 만료 여부를 판정하는 건 AI Server가 아니라 백엔드입니다. 만료는 조회 시점마다 바뀌는 값이라, 백엔드가 1차 필터링(보관함 조회) 때마다 "오늘"과 비교해 계산합니다.
- 엔드포인트별 타임아웃 — `analyze-video` 120초 / `verify-place` 60초 / `extract` 30초 / `recommend-courses` 90초 / `embed-places` 60초
- 요청 본문 상한 256KB. `candidates`·`places`·`history_place_ids` 리스트는 각각 최대 50개·100개·50개.