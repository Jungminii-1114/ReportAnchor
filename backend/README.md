# ReportAnchor 로컬 데몬 (MVP)

단일 Python 로컬 서버: `FastAPI + uvicorn` · `SQLite WAL` 잡 큐 · `PyMuPDF` 파싱(자식 프로세스 격리) · `Kiwi + BM25` 검색 · 단일 AI 연결 모듈(`Ollama /api/chat`, 허용 주소 목록 강제).
설계 기준: [로컬 데몬 · 사내망 AI · DB·백업 아키텍처](../docs/local-daemon-and-deployment.md)

## 실행

**원클릭 (리포 루트에서):** `.\start-local.ps1` — 필요 시 프론트엔드 빌드·`uv sync` 후 데몬을 실행하고 브라우저를 연다(`-Port 8800`, `-NoBrowser`, `-RebuildFrontend` 지원, 종료: Ctrl+C).

**수동 (backend 폴더에서):**

```powershell
uv sync                                  # .venv 생성·의존성 설치
.venv\Scripts\python -m reportanchor     # http://127.0.0.1:8765
.venv\Scripts\python -m pytest           # 테스트 (실제 Ollama 호출 없음)
```

| 환경 변수 | 의미 |
|---|---|
| `REPORTANCHOR_PORT` | 포트 (기본 `8765`) |
| `REPORTANCHOR_DATA_DIR` | 데이터 위치 (기본 `backend/.data`: `reportanchor.db`, `pdfs/`, `artifacts/`) |
| `REPORTANCHOR_ALLOWED_OLLAMA_HOSTS` | 추가 허용 AI 서버 주소(쉼표 구분, 예: 사내 Ollama 서버) |
| `REPORTANCHOR_OLLAMA_MODEL` | 로컬 Ollama 기본 모델 이름 |
| `OLLAMA_API_KEY` | 개발용 Ollama Cloud 키 (공개 자료에만 사용) |

- 시작 시 로컬 Ollama(`127.0.0.1:11434`)가 응답하면 `local`, 아니면 키가 있을 때 `cloud`(`deepseek-v4.1-flash`)로 준비한다. 둘 다 없으면 검색 전용으로 동작한다. `POST /api/model`로 바꾼 설정은 DB에 저장되어 다음 실행에도 유지된다.
- `../frontend/dist`가 있으면 `/`에서 정적 UI(SPA)를 함께 제공한다.
- 페이지 번호는 1부터 시작한다(`/api/documents/{id}/pages/1`).
