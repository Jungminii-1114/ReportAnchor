# ReportAnchor Design Files

FigCraft 작업 파일과 내보내기 이미지의 버전 규칙입니다.

## 현재 작업 파일

`reportanchor-workspace.figcraft`

| 페이지 | 내용 |
|---|---|
| `01 · Foundation — v0.1` | 최초 디자인 언어 보드. 기록용으로만 유지 |
| `02 · Structure — v3` | HIG식 소스 목록 + 문서 + 인스펙터 구조 (1440×960) |
| `03 · Material 3` | M3를 적용한 현재 워크스페이스. `Before — current` 프레임은 v3 비교용 |

## 현재 내보내기

| 파일 | 내용 |
|---|---|
| `material3-workspace.png` | M3 워크스페이스 전체 화면 (1x) |
| `sidebar-explorer.png` | M3 사이드바 2x 크롭 |
| `structure-skeleton.png` | v3 구조 화면. M3 이전 비교용 |
| `design-language.png` | v0.1 디자인 언어 보드 |

내보내기 파일은 항상 최신 상태만 루트에 둡니다. 이전 상태의 이미지는 `versions/`의 `.figcraft`에서 다시 추출합니다.

## 버전

| 버전 | 날짜 | 변경 | 파일 |
|---|---|---|---|
| v1 | 2026-11-04 | 최초 구조 스켈레톤 · top bar 포함 | `versions/reportanchor-workspace_v1_2026-11-04.figcraft` |
| v2 | 2026-11-04 | top bar 제거 · 탭 최상단 · 사이드바 아이콘 브랜드 | `versions/reportanchor-workspace_v2_2026-11-04.figcraft` |
| v3 | 2026-11-04 | 브레드크럼 페이지 · 중복 줌 제거 | `versions/reportanchor-workspace_v3_2026-11-04.figcraft` |
| v4 | 2026-11-04 | Material 3 적용 · 중복 크럼/루트 제거 · 검토 패널 단일 스크롤 | `versions/reportanchor-workspace_v4_2026-11-04.figcraft` |

## Material 3 기준

- 웹앱이므로 **Material 3**를 기준으로 하고, 문서 도구의 3열 구조는 HIG를 따릅니다.
- 8dp 그리드, M3 타입 스케일, 3px 탭 인디케이터, tonal surface, 상태 레이어를 사용합니다.
- 상태 색은 항상 텍스트 라벨과 함께 씁니다: `뒷받침` · `모순` · `근거 부족` · `부분 답변`.
- 서체: UI `Noto Sans KR`, 문서 `Noto Serif KR`, 메타 `JetBrains Mono`.
- 타입: 라벨 10–13, 본문 14/24, 제목 16, 질문 16, 지면 H1 24, 통계 32.
- 색 역할: primary `#1F6B54`, primaryContainer `#D5EBE0`, secondaryContainer `#E3F1EA`, tertiary `#9A5B00`, error `#B42318`, surface `#FFFDF8`, outlineVariant `#E4DED1`.

## 중복 정리 원칙

- 문서명은 **열린 탭**과 **탐색기 문서 행** 두 곳만 허용합니다.
- 페이지 위치와 줌은 **리더 바** 한 곳만 허용합니다.
- 사이드바에 가상 루트 행을 두지 않습니다. `자료`가 최상위입니다.
- 우측 패널은 탭 없이 하나의 스크롤 인스펙터로 유지합니다. 섹션 제목이 구조를 담당합니다.
- 노트 미리보기는 연결 목록에 넣지 않고 노트 섹션에서만 보여줍니다.

## 레거시

- `legacy/panel-modes/` — 우측 패널 확장 모드 보드와 4개 패널, 내보내기 이미지
  - `근거 대조`, `기준 검증`, `노트`, `검토 기록`
  - 2026-11-04 기준 메인 범위에서 제외. 삭제하지 않고 보관만 합니다.

## 규칙

1. 작업은 루트의 `reportanchor-workspace.figcraft`에서만 합니다.
2. 구조 변경이 승인될 때마다 `versions/`에 `reportanchor-workspace_vN_YYYY-MM-DD.figcraft`로 스냅샷을 남깁니다.
3. `versions/`와 `legacy/` 안의 파일은 수정하지 않습니다.
4. 더 이상 쓰지 않는 산출물은 삭제하지 않고 `legacy/<기능>/`으로 옮깁니다.
5. Git 커밋은 전체 이력을, `versions/` 스냅샷은 리뷰 가능한 마일스톤을 담당합니다.
