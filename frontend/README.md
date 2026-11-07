# 프론트엔드 UI

Chrome Extension Manifest V3 + React 기반 UI입니다. 현재 PDF 감지와 AI 분석은 연결하지 않았으며 예시 데이터로 동작합니다.

| 폴더 | 용도 |
| --- | --- |
| [report-agent-chat](./report-agent-chat) | 현재 채팅형 UI. 이후 개발은 이 버전에서 진행합니다. |
| [legacy/report-agent](./legacy/report-agent) | 보관용 4단계 UI. 문서 확인 → 평가 조건 → 분석 진행 → 결과 확인 형태입니다. |

## 설치 없이 미리보기

각 폴더의 `preview.html`을 다운로드해서 Chrome으로 여세요. GitHub 파일 화면에서는 HTML 코드만 보이며, 파일 하나만 내려받으면 예시 PDF와 UI를 함께 확인할 수 있습니다.

미리보기의 Chrome 창 모형과 예시 PDF는 실제 확장 화면에 포함되지 않습니다. 실제 확장은 사이드 패널만 표시합니다. 실제 기능의 구현 여부는 [기능 점검 문서](./report-agent-chat/FEATURE-AUDIT.md)를 참고하세요.

## 현재 버전 실행

Node.js 20.19 이상, Chrome 141 이상이 필요합니다.

```sh
cd frontend/report-agent-chat
npm install
npm run dev
```

Chrome 확장으로 설치하려면:

```sh
npm run build
```

1. Chrome에서 `chrome://extensions`를 엽니다.
2. 개발자 모드를 켜고 **압축해제된 확장 프로그램을 로드합니다**를 선택합니다.
3. `frontend/report-agent-chat/dist` 폴더를 지정합니다.
4. Report Agent Chat 아이콘을 고정하고 눌러 사이드 패널을 엽니다.

패널 안의 **PDF 크게 보기**는 패널을 닫습니다. Chrome 확장 아이콘을 누르면 다시 열립니다. **전체화면**은 대화를 새 탭에서 넓게 보며, 브라우저 F11 모드는 아닙니다.

## 저장소에 포함하는 파일

소스, 설정, 의존성 잠금 파일, 설명 문서와 공유용 `preview.html`을 포함합니다. `node_modules`, `dist`, ZIP, 임시 테스트 프로필은 포함하지 않습니다. UI 변경 후 빌드하면 `preview.html`도 갱신됩니다.
