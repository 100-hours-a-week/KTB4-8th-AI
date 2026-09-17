# AI 서비스 인프라 연동 결정 및 잔여 확인 사항

## 목적

V1 MVP의 AI 서비스 실행·배포 기준을 인프라팀과 AI팀이 공유하기 위한 문서다. Dockerfile과 CI는 인프라팀이 작성하고, 운영 Compose는 Cloud 저장소에서 관리한다.

AI 위키, 사용자 확인, 인프라팀 결정은 서로 다른 근거이므로 아래에서 출처를 구분한다. API 요청·응답 계약은 별도 담당자가 관리하므로 이 문서에서 변경을 요청하지 않는다.

상태 표기는 다음과 같다.

- **AI 위키 확정**: AI 설계 문서에 명시된 값
- **사용자 확인**: 이번 V1 MVP 범위로 직접 확인받은 값
- **인프라팀 결정**: 애플리케이션 요구사항을 바꾸지 않는 범위에서 인프라팀이 정한 값
- **미확정/초안**: 위키나 구현 코드에 근거가 없어 추가 확인이 필요한 값

## 1. 인프라팀 결정안

다음 항목은 인프라팀에서 아래 기준으로 진행한다. AI 애플리케이션 구현에 문제가 있을 때만 AI팀이 의견을 준다.

| 항목 | 결정안 |
| --- | --- |
| Python | 현재 코드 기준 3.13 |
| 패키지 설치 | `uv.lock` 기반 `uv sync --frozen` |
| Dockerfile | AI 저장소 루트에서 관리 |
| CI | `.github/workflows/ci.yml`에서 관리 |
| 운영 Compose | Cloud 저장소에서 관리 |
| 기본 포트 | 컨테이너 내부 `8000` |
| 외부 공개 | AI 포트는 공개하지 않고 Backend/Worker만 내부 접근 |
| 실행 명령 | Dockerfile에서 관리하고 Compose에서 중복 선언하지 않음 |
| 이미지 저장소 | AWS ECR |
| 이미지 태그 | Git commit SHA |
| AWS 인증 | GitHub OIDC |
| 컨테이너 사용자 | non-root |
| 시크릿 | 이미지에 포함하지 않고 런타임에 전달 |
| 로그 | 컨테이너 로그 수집은 CloudWatch Logs 초안. AI 위키의 Grafana Cloud 로그·메트릭·트레이스 구성과 역할 정리 필요 |
| 배포 책임 | AI 저장소는 Build/Push, Cloud 저장소는 배포 담당 |
| 애플리케이션 실행 경로 | `app.main:app` — AI 위키의 `app/main.py` 구조와 README 실행 예시 기준 |
| V1 비동기 처리 | Compose 서비스 `worker`가 작업을 관리하고 AI API를 호출 — 사용자 확인 |
| V1 AI Worker·Redis Streams | 사용하지 않음 — 사용자 확인. AI 위키의 V3 구조와 구분 |

Python 3.13은 `pyproject.toml`, `.python-version`, `uv.lock`, Dockerfile, CI에 반영되어 있다.

### 시크릿값 이름 및 ID — 사용자 확인

| 환경변수 | Secret ID | Backend API | Worker | AI API |
| --- | --- | --- | --- | --- |
| `DB_SECRET_ID` | `keepgo/prod/database` | ✓ | ✓ | — |
| `GOOGLE_OAUTH_SECRET_ID` | `keepgo/prod/google-oauth` | ✓ | ✓ | — |
| `JWT_SECRET_ID` | `keepgo/prod/jwt` | ✓ | — | — |
| `MAPS_SECRET_ID` | `keepgo/prod/maps` | ✓ | — | ✓ |
| `GEMINI_SECRET_ID` | `keepgo/prod/gemini` | — | — | ✓ |

AI API는 Backend가 전달한 데이터만 처리하고 업무 DB에 직접 접근하지 않으므로 `DB_SECRET_ID`가 필요하지 않다. 환경변수에는 실제 시크릿값이 아니라 Secret ID를 전달하며, 각 애플리케이션은 IAM 권한으로 Secrets Manager에서 값을 조회한다. AI API의 조회 코드는 아직 없으므로 Compose 설정은 구현 전까지 주석으로 둔다.

### 서비스·이미지·태그 이름 — 사용자 확인

| 담당 | Compose 서비스 이름 | ECR 이미지 이름 | 이미지 태그 환경변수 | Spring 프로필 |
| --- | --- | --- | --- | --- |
| Frontend | `web` | `keepgo-web` | `WEB_IMAGE_TAG` | 해당 없음 |
| Backend API | `backend` | `keepgo-backend` | `BACKEND_IMAGE_TAG` | `prod` |
| Worker | `worker` | `keepgo-worker` | `WORKER_IMAGE_TAG` | `worker` |
| AI | `ai-api` | `keepgo-ai` | `AI_IMAGE_TAG` | 해당 없음 |

Backend의 `prod` 프로필 이름은 확정됐지만 현재 `application-prod.yaml`이 없으므로 Compose에서는 구현 전까지 주석으로 둔다.

## 2. 확인 결과

### 1) 서버 실행 방식 — 일부 확정

- FastAPI 사용과 `app/main.py` 구조는 AI 위키에 명시되어 있다.
- Uvicorn과 `app.main:app` 실행 문자열은 README와 인프라 초안에서 정한 실행 방식이다.
- 실행 명령은 Dockerfile에서 관리하고 운영 Compose에서 중복 정의하지 않는다.
- 현재 `app/main.py`가 아직 없으므로 Dockerfile의 실행 명령은 주석 상태로 두고, 애플리케이션 추가 후 활성화한다.

### 2) LLM — 사용자 확인

- Provider: `google`
- 생성·영상 분석 모델: `gemini-3.5-flash-lite`
- V1에서는 로컬 LLM과 GPU 모델 서버를 사용하지 않는다.

### 3) 임베딩 — AI 위키 확정

- 임베딩 기능을 사용한다.
- Provider: `google`
- 모델: `gemini-embedding-001`
- Gemini 임베딩 API를 호출하며 컨테이너에서 로컬 임베딩 모델을 실행하지 않는다.

### 4) 벡터 DB — 일부 확정

- V1에서 Chroma를 사용한다.
- AI 프로세스 내부의 임베디드 방식으로 실행하는 것은 AI 위키 확정 사항이다.
- 저장 데이터가 MySQL에서 재생성 가능한 파생 인덱스라는 점도 AI 위키에 명시되어 있다.
- Docker volume 영속화 여부와 컨테이너 저장 경로는 위키에 없다. `/app/data/chroma`는 인프라 초안이므로 현재 Compose와 Dockerfile에서는 활성화하지 않는다.
- AI API를 여러 인스턴스로 확장할 때 Chroma 서버 모드 전환을 재검토한다.

### 5) 외부 API — AI 위키 확정

- AI 서버가 Gemini 생성·영상 분석 API와 Gemini 임베딩 API를 직접 호출한다.
- AI 서버가 장소 검증과 이동 시간 계산을 위해 네이버지도 API를 직접 호출한다.
- LangSmith 사용은 AI 위키에 명시되어 있다. 다만 V1에서 즉시 활성화할지와 실제 환경변수 계약은 아직 구현되지 않았다.

### 6) 로컬 모델과 파일 — V1 범위에서 확인

- V1 컨테이너는 시작 시 모델이나 대용량 파일을 다운로드하지 않는다.
- 로컬 LLM과 로컬 임베딩 모델을 실행하지 않는다.
- 모델 캐시 volume은 필요하지 않다.

### 7) 헬스체크 — AI 위키 요구사항, 구현 예정

- `GET /health`를 구현할 예정이다.
- 헬스체크에서는 Gemini·지도 API 같은 외부 서비스를 호출하지 않는다.
- 외부 API를 호출하지 않는 readiness 범위로 구현하되, Chroma를 필수 조건에 포함할지는 구현 시 확정한다.
- 현재 엔드포인트가 없으므로 Compose `healthcheck`와 `worker`의 `service_healthy` 의존 조건은 주석 상태로 둔다. 엔드포인트 구현 후 함께 활성화한다.

헬스체크 주기, timeout, retries 같은 운영값은 인프라팀이 구현 후 측정하여 정한다.

### 8) 환경변수와 시크릿 — Secret ID 확정, 애플리케이션 설정 일부 미확정

Secret ID 환경변수 이름과 값은 위의 팀 공통 표로 확정됐다. AI API에는 `MAPS_SECRET_ID`와 `GEMINI_SECRET_ID`만 필요하고 `DB_SECRET_ID`는 필요하지 않다. 모델·임베딩 등 비시크릿 설정 변수명은 `core/config.py` 구현 시 최종 확정한다.

| 변수명 | 용도 | 필수 여부 | 시크릿 여부 |
| --- | --- | --- | --- |
| `PORT` | AI API 내부 포트, 기본값 `8000` | 선택 | 아니요 |
| `LLM_PROVIDER` | 생성 모델 Provider, 값 `google` | 필수 | 아니요 |
| `GOOGLE_MODEL` | 생성·영상 분석 모델, 값 `gemini-3.5-flash-lite` | 필수 | 아니요 |
| `EMBEDDING_PROVIDER` | 임베딩 Provider, 값 `google` | 필수 | 아니요 |
| `EMBEDDING_MODEL` | 임베딩 모델, 값 `gemini-embedding-001` | 필수 | 아니요 |
| `GEMINI_SECRET_ID` | Gemini Secret ID, 값 `keepgo/prod/gemini` | 필수 | 아니요(Secret ID) |
| `MAPS_SECRET_ID` | 지도 API Secret ID, 값 `keepgo/prod/maps` | 필수 | 아니요(Secret ID) |
| `CHROMA_PERSIST_DIRECTORY` | Chroma 영속 저장 경로 후보 | 영속화 채택 시 | 아니요 |
| `LANGSMITH_TRACING` | LangSmith 추적 활성화 여부 | 선택 | 아니요 |
| `LANGSMITH_API_KEY` | LangSmith 인증 | 추적 사용 시 필수 | 예 |
| `LANGSMITH_PROJECT` | LangSmith 프로젝트 구분 | 선택 | 아니요 |

AI API가 IAM 권한과 Secret ID를 사용해 AWS Secrets Manager에서 실제 값을 조회하는 코드는 아직 구현되지 않았다. 구현 전까지 관련 Compose 환경변수는 확정값을 주석으로 보관한다.

### 9) Worker와 작업 큐 — V1 사용자 확인

- V1에서는 AI팀이 별도 Worker를 개발하지 않는다.
- Redis Streams를 사용하지 않는다.
- Compose 서비스 `worker`가 Backend DB의 작업 상태를 관리하고 기존 AI API 엔드포인트를 호출한다.
- AI API의 기존 호출 구조와 요청·응답 계약은 변경하지 않는다.
- AI 위키 최종 구조에는 AI worker와 Redis Streams가 있으므로, 이는 위키 일반 결론이 아니라 이번 V1 MVP 범위에서 사용자에게 확인받은 예외다.

### 10) 테스트 도구 — 미확정

- AI 위키에는 Ruff와 Pytest 사용 여부가 명시되어 있지 않다.
- 현재 `pyproject.toml`에도 Ruff·Pytest 의존성이 없고 테스트 코드가 없으므로 CI 단계는 주석 상태로 둔다.
- 테스트 도구를 도입한다면 외부 Gemini·네이버지도 API는 실제 호출하지 않고 mock 처리하는 방향을 제안한다.
- Ruff·Pytest 채택 여부는 개발팀 확인이 필요하다.



## 4. 남은 확인 사항

- API 요청·응답 스키마와 timeout은 API 계약 담당자와 별도 조율하며, AI팀에 인프라 질문으로 요청하지 않는다.
- Ruff·Pytest 채택 여부는 AI 위키에서 확인되지 않아 개발팀 확인이 필요하다.
- Chroma를 V1에서 volume으로 영속화할지, 재시작 시 Backend 원본에서 재색인할지 결정이 필요하다.
- AI API의 `core/config.py`와 AWS Secrets Manager 조회 코드를 구현해야 한다. Secret ID 이름과 값은 확정됐으며 비시크릿 설정 변수명만 구현 시 최종 확인한다.
- V1 로그는 CloudWatch Logs로만.
- LangSmith를 V1부터 활성화할지와 인증·프로젝트 환경변수 이름을 구현 시 확정해야 한다.
- CPU·메모리, Uvicorn worker 수, 헬스체크 세부 주기, volume 크기는 구현 후 측정값으로 조정한다.
- AI worker와 Redis Streams는 V1 범위가 아니며 이후 확장 단계에서 다시 검토한다.

## 5. 공유용 요약

> V1 MVP 인프라 연동 기준을 다음과 같이 확정했습니다.
>
> 1. FastAPI와 `app/main.py`는 위키 확정, Uvicorn의 `app.main:app` 실행은 인프라 기준
> 2. 생성·영상 분석 `gemini-3.5-flash-lite`는 사용자 확인
> 3. 임베딩 `gemini-embedding-001`은 위키 확정
> 4. 임베디드 Chroma는 위키 확정, volume 영속화와 저장 경로는 미확정
> 5. Gemini와 네이버지도 API를 AI 서버가 직접 호출
> 6. V1 로컬 모델·GPU·대용량 모델 다운로드 없음
> 7. Secret ID 이름·값과 서비스별 사용 범위는 확정, AI API의 Secrets Manager 조회 코드는 구현 예정
> 8. 외부 API를 호출하지 않는 `GET /health` 추가 예정이며, 구현 전까지 Compose 설정은 주석 유지
> 9. V1 비동기 작업은 Compose 서비스 `worker`가 관리하며 AI Worker·Redis Streams는 사용하지 않음
> 10. Ruff/Pytest는 위키에 근거가 없어 미확정이며, 채택 여부 확인 전까지 CI 단계는 주석 유지
>
> Python 3.13, `uv.lock` 기반 설치, ECR SHA 태그, 내부 네트워크, 로그·배포 설정은 인프라팀 기준으로 진행합니다. API 계약은 별도 담당자와 조율합니다.
