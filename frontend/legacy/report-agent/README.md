# Report Agent

Chrome Manifest V3 사이드 패널의 UI 목업입니다. 2026-11-02 팀 피드백을 반영하여 업로드 방식에서 **문서 확인 → 평가 조건 입력 → 분석 진행 → 결과 확인** 흐름으로 변경했습니다.

## 화면 바로 보기

`preview.html`을 Chrome에서 여세요. 이 파일 하나에 React, CSS, 예시 PDF가 포함되어 있어 설치나 인터넷 연결 없이 다른 사람에게 전달할 수 있습니다.

- 왼쪽: 6페이지짜리 실제 예시 PDF. 브라우저의 기본 PDF 뷰어로 표시합니다.
- 오른쪽: Report Agent 사이드 패널 예시 UI.
- **패널 접기 / Report Agent 열기**: 패널을 숨기거나 다시 엽니다.
- **PDF 넓게 보기 / 검토 화면으로 돌아가기**: PDF에 집중하거나 검토 화면으로 돌아갑니다. OS 전체 화면으로 전환하는 기능은 아닙니다.
- 상단의 **크게 보기(⤢)** 버튼을 누르면 Report Agent UI가 새 탭에 열립니다. **사이드 패널로 돌아가기**로 원래 화면에 복귀합니다. PDF 원문을 넓히는 버튼과는 별개입니다.
- 질문·조건·현재 단계·결과 필터를 두 화면이 공유합니다. 한쪽에서 수정하면 다른 쪽에도 반영됩니다. 미리보기는 같은 파일의 localStorage에 저장하므로 새로고침 후에도 유지됩니다.
- 760px 이하의 좁은 화면에서는 아래 **PDF 원문 / Report Agent** 탭으로 전환합니다.
- 미리보기를 새 탭에서 처음 열면 **탐색 실패** 화면으로 시작합니다. **다시 찾기**를 누르면 **찾는 중 → 문서 찾음**으로 약 1.5초 후 전환됩니다. 별도의 상태 선택 칸은 없습니다. 찾는 중·실패 상태에서는 다음 단계로 이동할 수 없습니다. 같은 탭의 새로고침이나 크게 보기는 진행 중인 상태를 이어갑니다.

미리보기 상단의 탭·주소창은 사용 상황을 설명하는 장식입니다. 실제 Chrome UI를 조작하거나 PDF를 감지하지 않습니다. PDF의 확대·페이지 이동 등 기본 기능은 브라우저의 PDF 뷰어가 제공합니다.

`index.html`은 개발 서버나 확장 프로그램에서 실행하는 진입점입니다. 더블클릭할 때는 `preview.html`을 사용하세요.

## 개발 실행

Node.js 20.19 이상 또는 22.12 이상을 설치하고 프로젝트 폴더에서 실행합니다.

```sh
npm install
npm run dev
```

터미널에 표시되는 로컬 주소에서는 **확장 패널 UI만** 표시합니다. 좌우 분할 미리보기는 `preview.html`에만 적용됩니다. 소스 수정 후 아래 명령으로 확장 프로그램과 단일 파일 미리보기를 함께 생성합니다.

```sh
npm run build
```

pnpm 사용 시 `pnpm install`, `pnpm run dev`, `pnpm run build`도 가능합니다. `scripts/build-preview.mjs`는 예시 PDF를 포함하는 미리보기를 생성합니다.

## Chrome에 설치

1. 빌드하거나 함께 제공된 `dist` 폴더를 사용합니다.
2. Chrome 주소창에 `chrome://extensions`를 입력합니다.
3. **개발자 모드**를 켭니다.
4. **압축해제된 확장 프로그램 로드**에서 `dist` 폴더를 선택합니다.
5. 확장 프로그램 아이콘을 고정하고 Report Agent를 누릅니다.
6. 실제 Chrome 사이드 패널에는 Report Agent UI만 열립니다. PDF는 기존 브라우저 탭에서 열어 보세요.

Chrome 116 이상이 필요합니다. 수정 후 다시 빌드하고 확장 관리 화면에서 확장 프로그램을 새로고침하세요. 기존 버전을 사용 중이라면 storage 권한이 추가된 최신 dist를 다시 로드해야 합니다. 사이드 패널 등록은 [Chrome 공식 sidePanel 문서](https://developer.chrome.com/docs/extensions/reference/api/sidePanel)를 따릅니다. 권한은 `sidePanel`과 `storage`를 사용합니다. `storage`는 패널과 새 탭의 입력 내용을 공유하기 위한 권한입니다. 탭 생성·활성화에 별도의 `tabs` 권한은 필요하지 않아 추가하지 않았습니다.

## 구현 범위

실제 PDF 탭 감지·파일 접근·AI 분석·API 통신·다운로드·근거 페이지 연동은 구현하지 않았습니다. 실제 확장의 기본 상태는 예시 PDF를 찾은 상태이고, 단일 파일 미리보기는 처음 열 때 탐색 실패 상태로 시작합니다. 찾는 중·탐색 실패 UI도 구현되어 있으며, 실제 상태는 추후 탭 감지 모듈에서 전달하면 됩니다. 입력 질문이나 평가 조건과 관계없이 고정 결과 5개를 표시합니다.

예시 PDF는 UI 확인을 위해 새로 작성한 가상 문서이며 실제 사업 자료가 아닙니다. 결과 카드의 근거 문구와 페이지는 예시 PDF의 2~6페이지에 맞췄습니다. 원문 근거 버튼은 아직 안내만 표시합니다.

실제 확장 프로그램은 chrome.storage.session에 검토 상태를 저장하고 storage.onChanged로 두 화면을 동기화합니다. 같은 브라우저 세션에서 패널·확장 페이지를 닫았다 다시 열거나 새로고침해도 유지됩니다. 브라우저 재시작, 확장 비활성화·새로고침 시에는 초기화됩니다. 현재 목업은 하나의 검토 세션을 공유하며, 다중 문서 지원 시 문서별 세션 키로 확장해야 합니다.

크게 보기는 모니터 전체 화면(F11)이 아니라 새 탭의 넓은 화면입니다. 실제 확장의 돌아가기 버튼은 원래 탭을 활성화하고 사이드 패널을 엽니다. 큰 화면 탭은 남겨 두므로 필요하면 다시 선택할 수 있습니다. 미리보기에서는 돌아가기 버튼이 원래 창에 초점을 주고 새 탭을 닫습니다. 팝업 차단 시 안내가 표시됩니다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `package.json` | React, Vite, 아이콘, 빌드 도구와 실행 명령입니다. |
| `pnpm-lock.yaml` | 설치한 패키지 버전을 고정합니다. |
| `.npmrc`, `pnpm-workspace.yaml` | pnpm 설치 환경 설정입니다. |
| `index.html` | 확장 프로그램과 개발 서버에서 React를 실행하는 HTML입니다. |
| `vite.config.js` | 상대 경로 빌드와 패널 전용 모드를 설정합니다. |
| `public/manifest.json` | Manifest V3 사이드 패널 설정입니다. |
| `public/background.js` | 확장 아이콘으로 패널을 여는 동작입니다. |
| `public/sample-report.pdf` | 6페이지짜리 실제 예시 PDF입니다. |
| `src/main.jsx` | React 시작 지점과 CSS 로딩입니다. |
| `src/App.jsx` | 공유 검토 상태를 각 화면에 연결하고 새 탭 보기와 알림을 관리합니다. |
| `src/services/reviewState.js` | 검토 상태 저장·복원과 화면 간 동기화입니다. 확장은 session storage, 미리보기는 localStorage를 사용합니다. |
| `src/services/expandedView.js` | 새 탭 열기와 원래 패널로 복귀하는 동작입니다. |
| `src/expanded.css` | 큰 화면 레이아웃과 헤더 버튼의 반응형 스타일입니다. |
| `src/components/Layout.jsx` | 공통 헤더·단계 표시·알림입니다. |
| `src/components/PreviewShell.jsx` | 단일 파일 미리보기의 PDF와 패널 분할·접기 UI입니다. |
| `src/screens/DocumentScreen.jsx` | 문서 확인의 찾는 중·찾음·실패 상태와 재시도 UI입니다. |
| `src/screens/ConditionsScreen.jsx` | 질문·조건 편집 화면입니다. |
| `src/screens/ProgressScreen.jsx` | 고정 75% 분석 진행 화면입니다. |
| `src/screens/ResultsScreen.jsx` | 보고서 형태의 결과 요약·판단 설명·근거·필터입니다. |
| `src/data/mockData.js` | 예시 질문·조건·분석 결과입니다. |
| `src/data/samplePdf.js` | 단일 파일 미리보기를 위한 PDF의 Base64 데이터입니다. |
| `src/styles.css` | 공통 UI 스타일입니다. |
| `src/document.css` | 문서 확인의 3가지 상태 스타일입니다. |
| `src/results.css` | 차분한 보고서 형태의 검토 결과 스타일입니다. |
| `src/preview.css` | 미리보기의 브라우저 프레임·분할 화면 스타일입니다. |
| `scripts/build-preview.mjs` | 미리보기 전용 코드를 포함해 `preview.html`을 생성합니다. |
| `preview.html` | 파일 하나로 공유할 수 있는 미리보기 결과물입니다. |
| `dist/` | Chrome에서 로드할 빌드 결과입니다. |
| `VERIFICATION.md` | 검증 범위와 결과입니다. |

## 추후 연결 지점

`src/services/reportApi.js` 등에 API 요청을 모으고 `App.jsx`에서 상태를 관리한 뒤 화면에 props로 전달하는 구조로 확장할 수 있습니다. 실제 탭 감지는 별도 Chrome 연동 모듈로 분리하고 필요한 권한만 추가하세요. `DocumentScreen`의 예시 문서와 `ResultsScreen`의 고정 결과를 실제 데이터 props로 교체하면 됩니다. 백엔드 연결 시 서버 주소, 확장 권한, CORS를 맞추고 API 키는 프론트엔드에 넣지 마세요.
