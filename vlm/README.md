# VLM 문서 보완 파이프라인

현재 실행 코드는 **[reportanchor_pipeline](reportanchor_pipeline/README.md)** 입니다.

PDF 전체 → PyMuPDF 기본 추출 → Docling 표·레이아웃/필요한 EasyOCR → Python 영역 선정 → 선택적 OCR 재검토 또는 Ollama Cloud VLM → 출처·검증 상태 저장.

- [통합 실행 방법과 선택 규칙](reportanchor_pipeline/README.md)
- [구현 상태와 남은 문제](reportanchor_pipeline/CURRENT_STATUS.md)
- [RAG 연결용 필드 설명](reportanchor_pipeline/RAG_HANDOFF.md)
- [가상 데이터로 만든 RAG 후보 샘플 3개](reportanchor_pipeline/examples/rag_candidates.sample.jsonl)

코드 연결과 VLM 추출 품질 검증은 구분합니다. 생성 길이 제한, 원문 임의 수정, 표 구조 표현의 불일치가 남아 있어 검증된 최종 RAG 입력으로 간주하면 안 됩니다. 실제 문서 결과·캐시·검증 산출물은 이 변경에 포함하지 않았습니다.

## 기존 실험 파일

기존 `run_pipeline.py`, `pipeline/`, `run/`, `selection_v4/`, `experiments/selection_full/`는 이전 v4 실험과 재현 자료입니다. 최신 파이프라인의 선행 실행 단계가 아닙니다. 실험 비교 근거를 보존하기 위해 이번 변경에서는 삭제하지 않았습니다.

새 결과·캐시·원본 PDF·API 키는 커밋하지 않습니다. 기존에 추적된 실험 산출물은 그대로 남아 있습니다.
