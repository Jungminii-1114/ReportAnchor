# ReportAnchor Design Files

FigCraft 작업 파일과 내보내기 이미지의 버전 규칙입니다.

## 현재 작업 파일

- `reportanchor-workspace.figcraft` — 메인 작업 파일
  - `01 · Design Foundation` — 컬러 · 타이포 · 간격
  - `02 · Structure` — 문서 + 검토 워크스페이스 (1440×960)

## 현재 내보내기

| 파일 | 내용 |
|---|---|
| `design-language.png` | 디자인 언어 보드 |
| `structure-skeleton.png` | 워크스페이스 전체 화면 |
| `sidebar-explorer.png` | 사이드바 2x 크롭 |

내보내기 파일은 항상 최신 상태만 루트에 둡니다. 이전 상태의 이미지는 `versions/`의 `.figcraft`에서 다시 추출합니다.

## 버전

| 버전 | 날짜 | 변경 | 파일 |
|---|---|---|---|
| v1 | 2026-11-04 | 최초 구조 스켈레톤 · top bar 포함 | `versions/reportanchor-workspace_v1_2026-11-04.figcraft` |
| v2 | 2026-11-04 | top bar 제거 · 탭 최상단 · 사이드바 아이콘 브랜드 | `versions/reportanchor-workspace_v2_2026-11-04.figcraft` |
| v3 | 2026-11-04 | 브레드크럼 페이지 · 중복 줌 제거 | `versions/reportanchor-workspace_v3_2026-11-04.figcraft` |

## 레거시

- `legacy/panel-modes/` — 우측 패널 확장 모드 보드와 4개 패널, 내보내기 이미지
  - `근거 대조`, `기준 검증`, `노트`, `검토 기록`
  - 2026-11-04 기준 메인 범위에서 제외. 삭제하지 않고 보관만 합니다.

## 규칙

1. 작업은 루트의 `reportanchor-workspace.figcraft`에서만 합니다.
2. 구조 변경이 승인될 때마다 `versions/`에 `reportanchor-workspace_vN_YYYY-MM-DD.figcraft`로 스냅샷을 남깁니다.
3. `versions/`와 `legacy/` 안의 파일은 수정하지 않습니다.
4. 더 이상 쓰지 않는 산출물은 삭제하지 않고 `legacy/<기능>/`으로 옮깁니다.
5. Git 커밋은 전체 이력를, `versions/` 스냅샷은 리뷰 가능한 마일스톤을 담당합니다.
