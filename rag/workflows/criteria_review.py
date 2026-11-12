"""T-02: N-05 → 기준별 N-01/N-02/N-06 → N-03 → 기준별 검증 결과.

재검색·자동 보정·전체 합격 규칙은 추가하지 않는다. 기준별 EvidenceBundle을
격리해 N-03으로 검증하고 supported인 조건만 충족/미충족 판정을 유지한다.
N-10 라우팅·권한 판단, N-11 사용자 표현, 웹 UI 연결은 포함하지 않는다.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
from typing import TypedDict
from langgraph.graph import START, END, StateGraph

from rag.contracts import (
    ClaimVerification, ContextExpansionConfig, CriteriaReviewResult, CriteriaReviewTask,
    CriteriaSet, CriterionExecutionError, CriterionFinding, CriterionVerdict, DocumentRef,
    EvidenceBundle, EvidenceSource, NodeResult, RetrievalCandidate, RetrievalCandidates, RetrievalRequest,
    ReviewDraft, ReviewedCondition, ReviewedCriterion, VerificationReport, Verdict, UnprocessedCriterion,
)
from rag.llm.ollama_client import OllamaClient
from rag.nodes._criteria_support import (
    MAX_CRITERIA, MAX_CRITERION_CHARS, aggregate, failure, text, validate_bundle, validate_criterion,
)
from rag.nodes.structure_criteria import StructureCriteriaNode
from rag.nodes.evaluate_criterion import EvaluateCriterionNode, claim_id_for, grounding_draft_claim
from rag.nodes.get_source_context import build_chunk_lookup, get_source_context, load_block_lookup
from rag.nodes.retrieve_evidence import retrieve_evidence
from rag.nodes.verify_grounding import VerifyGroundingNode
from rag.retrieval.bm25 import BM25Retriever, load_chunks
from rag.workflows.document_qa import _checked, _error_from


class CriteriaReviewState(TypedDict, total=False):
    task: CriteriaReviewTask
    validation_error: CriterionExecutionError
    n05: NodeResult[CriteriaSet]
    index: int
    n01: NodeResult[RetrievalCandidates] | None
    n02: NodeResult[EvidenceBundle] | None
    n06: NodeResult[CriterionFinding] | None
    records: list[dict]
    result: CriteriaReviewResult


def _contract_failure():
    return failure("criteria_workflow_contract", "NODE_CONTRACT_VIOLATION",
                   "요청·기준·조건·문서 범위·근거 참조 계약을 확인하세요.")


class CriteriaReviewWorkflow:
    def __init__(self, retriever, chunk_lookup, block_lookup, client: OllamaClient, *,
                 structure_node=None, evaluation_node=None, verifier=None, top_k=3,
                 retrieval_profile="bm25-baseline-v1", context_profiles=None):
        if type(top_k) is not int or not 1 <= top_k <= 20:
            raise ValueError("top_k는 1~20 정수여야 합니다.")
        self.retriever, self.chunk_lookup, self.block_lookup = retriever, chunk_lookup, block_lookup
        self.structure_node = structure_node if structure_node is not None else StructureCriteriaNode(client)
        self.evaluation_node = evaluation_node if evaluation_node is not None else EvaluateCriterionNode(client)
        self.verifier = verifier if verifier is not None else VerifyGroundingNode(client)
        self.top_k, self.retrieval_profile = top_k, retrieval_profile
        self.context_profiles = (dict(context_profiles) if context_profiles is not None else
                                 {"fixed-context-v1": ContextExpansionConfig()})
        if (not text(retrieval_profile) or not self.context_profiles or any(
                not text(k) or not isinstance(v, ContextExpansionConfig) for k, v in self.context_profiles.items())):
            raise ValueError("검색·원문 프로필 설정 오류")
        self.graph = self._build_graph()

    def _build_graph(self):
        g = StateGraph(CriteriaReviewState)
        for name, method in (("validate_input", self._validate), ("N-05", self._structure),
                ("N-01", self._retrieve), ("N-02", self._context), ("N-06", self._evaluate),
                ("collect_criterion", self._collect), ("N-03", self._verify), ("assemble_result", self._assemble)):
            g.add_node(name, method)
        g.add_edge(START, "validate_input")
        g.add_conditional_edges("validate_input", lambda s: "assemble_result" if s.get("validation_error") else "N-05")
        g.add_conditional_edges("N-05", lambda s: "N-01" if s["n05"].status != "error" and s["n05"].data.criteria else "assemble_result")
        g.add_conditional_edges("N-01", lambda s: "N-02" if s["n01"].status != "error" and s["n01"].data.candidates else "collect_criterion")
        g.add_conditional_edges("N-02", lambda s: "N-06" if s["n02"].status != "error" and s["n02"].data.evidence_items else "collect_criterion")
        g.add_edge("N-06", "collect_criterion")
        g.add_conditional_edges("collect_criterion", lambda s: "N-01" if s["index"] < len(s["n05"].data.criteria) else "N-03")
        g.add_edge("N-03", "assemble_result"); g.add_edge("assemble_result", END)
        return g.compile(name="T-02 Criteria Review")

    def run(self, task: CriteriaReviewTask) -> CriteriaReviewResult:
        return self.run_with_state(task)["result"]

    def run_with_state(self, task: CriteriaReviewTask) -> CriteriaReviewState:
        return self.graph.invoke({"task": deepcopy(task), "index": 0, "records": []},
                                 config={"recursion_limit": MAX_CRITERIA * 5 + 10})

    def _validate(self, state):
        t = state["task"]
        valid = isinstance(t, CriteriaReviewTask)
        if valid:
            valid = (text(t.request_ref) and text(t.question) and isinstance(t.criteria_texts, list)
                and len(t.criteria_texts) <= MAX_CRITERIA
                and all(text(v) and len(v) <= MAX_CRITERION_CHARS for v in t.criteria_texts)
                and isinstance(t.document_scope, list) and all(isinstance(d, DocumentRef) for d in t.document_scope)
                and t.retrieval_profile == self.retrieval_profile and isinstance(t.context_profile, str)
                and t.context_profile in self.context_profiles)
        if valid:
            try:
                for d in t.document_scope: d.__post_init__()
            except (TypeError, ValueError): valid = False
        return {} if valid else {"validation_error": CriterionExecutionError(None, "T-02", "INVALID_CRITERIA_REVIEW_TASK", False)}

    def _structure(self, state):
        task = state["task"]
        r = _checked(self.structure_node.run(task.request_ref, task.criteria_texts), CriteriaSet, task.request_ref)
        if r.status != "error":
            try:
                if not isinstance(r.data.criteria, list) or not isinstance(r.data.unprocessed_criteria, list):
                    raise ValueError("기준 목록 오류")
                seen, ids, condition_ids = set(), set(), set()
                for c in r.data.criteria:
                    validate_criterion(c); i = c.input_index
                    if (i >= len(task.criteria_texts) or i in seen or c.criterion_id in ids
                            or c.original_text != task.criteria_texts[i]): raise ValueError("원본 기준 대응 오류")
                    for condition in c.conditions:
                        if condition.condition_id in condition_ids: raise ValueError("조건 ID 중복")
                        condition_ids.add(condition.condition_id)
                    seen.add(i); ids.add(c.criterion_id)
                for u in r.data.unprocessed_criteria:
                    if (not isinstance(u, UnprocessedCriterion) or type(u.input_index) is not int
                            or not 0 <= u.input_index < len(task.criteria_texts) or u.input_index in seen
                            or u.original_text != task.criteria_texts[u.input_index]
                            or u.reason_code not in ("unsupported_structure", "needs_clarification") or not text(u.reason)):
                        raise ValueError("미처리 기준 대응 오류")
                    seen.add(u.input_index)
                if seen != set(range(len(task.criteria_texts))): raise ValueError("기준 누락")
                expected = ("partial_criteria" if r.data.criteria and r.data.unprocessed_criteria else
                    None if r.data.criteria else "unsupported_structure" if r.data.unprocessed_criteria and
                    all(u.reason_code == "unsupported_structure" for u in r.data.unprocessed_criteria) else "needs_clarification")
                if r.outcome != expected: raise ValueError("업무 결과 불일치")
            except (TypeError, ValueError, AttributeError): r = _contract_failure()
        return {"n05": r}

    def _criterion(self, state):
        return state["n05"].data.criteria[state["index"]]

    def _retrieve(self, state):
        t, c = state["task"], self._criterion(state)
        r = _checked(retrieve_evidence(RetrievalRequest(t.request_ref, c.original_text, t.document_scope,
            t.retrieval_profile), self.retriever, top_k=self.top_k), RetrievalCandidates, t.request_ref)
        if r.status != "error":
            try:
                allowed = {(d.document_id, d.document_version) for d in t.document_scope}; ids = set()
                if not isinstance(r.data.candidates, list): raise ValueError("후보 목록 오류")
                for candidate in r.data.candidates:
                    if (not isinstance(candidate, RetrievalCandidate) or candidate.chunk_id in ids
                            or (candidate.document_id, candidate.document_version) not in allowed):
                        raise ValueError("후보 범위 오류")
                    candidate.__post_init__(); ids.add(candidate.chunk_id)
            except (TypeError, ValueError, AttributeError): r = _contract_failure()
        return {"n01": r, "n02": None, "n06": None}

    def _context(self, state):
        t = state["task"]
        r = _checked(get_source_context(state["n01"].data, self.chunk_lookup, self.block_lookup,
            document_scope=t.document_scope, config=self.context_profiles[t.context_profile],
            context_profile_ref=t.context_profile), EvidenceBundle, t.request_ref)
        if r.status != "error":
            try:
                validate_bundle(self.verifier.client, t.request_ref, r.data)
                allowed = {(d.document_id, d.document_version) for d in t.document_scope}
                candidates = {c.chunk_id: (c.document_id, c.document_version) for c in state["n01"].data.candidates}
                if r.data.context_profile_ref != t.context_profile: raise ValueError("원문 프로필 오류")
                for item in r.data.evidence_items:
                    pair = item.document_id, item.document_version
                    if pair not in allowed or candidates.get(item.chunk_id) != pair: raise ValueError("원문 범위 오류")
            except (TypeError, ValueError, AttributeError): r = _contract_failure()
        return {"n02": r}

    def _evaluate(self, state):
        t, c = state["task"], self._criterion(state)
        r = _checked(self.evaluation_node.run(t.request_ref, c, state["n02"].data), CriterionFinding, t.request_ref)
        if r.status != "error":
            try:
                if not isinstance(r.data.verdict, CriterionVerdict) or any(
                        not isinstance(f.verdict, CriterionVerdict) for f in r.data.conditions): raise ValueError("판정 enum 오류")
                raw = json.dumps({"conditions": [{"condition_id": f.condition_id, "verdict": f.verdict,
                    "claim_text": f.claim_text, "reason": f.reason, "quote_ids": [q.quote_id for q in f.quotes]} for f in r.data.conditions]}, ensure_ascii=False, allow_nan=False)
                if EvaluateCriterionNode._parse(raw, t.request_ref, c, state["n02"].data) != r.data:
                    raise ValueError("조건/집계/주장 ID 오류")
            except (TypeError, ValueError, KeyError, AttributeError): r = _contract_failure()
        return {"n06": r}

    def _collect(self, state):
        record = {"criterion": self._criterion(state), "n01": state["n01"],
                  "n02": state.get("n02"), "n06": state.get("n06")}
        return {"records": [*state["records"], record], "index": state["index"] + 1}

    def _verify(self, state):
        records = deepcopy(state["records"])
        for record in records:
            evaluation = record.get("n06")
            if evaluation is None or evaluation.status == "error": continue
            claims = [grounding_draft_claim(record["criterion"], f) for f in evaluation.data.conditions
                      if f.verdict != CriterionVerdict.UNDETERMINED]
            if not claims: continue
            draft = ReviewDraft(state["task"].request_ref, claims, [])
            r = _checked(self.verifier.run(draft.request_ref, draft, record["n02"].data), VerificationReport, draft.request_ref)
            if r.status != "error":
                try:
                    if not isinstance(r.data.checks, list): raise ValueError("검증 목록 오류")
                    originals = {c.claim_id: c for c in claims}; seen = set()
                    for check in r.data.checks:
                        if (not isinstance(check, ClaimVerification) or not text(check.claim_id)
                                or check.claim_id not in originals or check.claim_id in seen
                                or not isinstance(check.verdict, Verdict) or not text(check.reason)
                                or not isinstance(check.evidence_refs, list) or any(not text(ref) for ref in check.evidence_refs)
                                or len(check.evidence_refs) != len(set(check.evidence_refs))
                                or set(check.evidence_refs) != set(originals[check.claim_id].evidence_refs)):
                            raise ValueError("검증 ID/참조 오류")
                        seen.add(check.claim_id)
                    failed = {item.ref for d in r.diagnostics.values() if d.status == "failed"
                              for item in d.failed_items if item.error_code != "NUMERIC_HINTS_FAILED"}
                    if seen & failed or seen | failed != set(originals): raise ValueError("검증 대상 누락/중복")
                except (TypeError, ValueError, AttributeError): r = _contract_failure()
            record["n03"] = r
        return {"records": records}

    def _assemble(self, state):
        t = state["task"]
        request_ref = t.request_ref if isinstance(t, CriteriaReviewTask) and text(t.request_ref) else ""
        if state.get("validation_error"):
            return {"result": CriteriaReviewResult(request_ref, "error", None, [], [], False, [state["validation_error"]])}
        n05 = state["n05"]
        if n05.status == "error":
            e = _error_from("N-05", n05)
            return {"result": CriteriaReviewResult(request_ref, "error", None, [], [], False,
                [CriterionExecutionError(None, e.node, e.error_code, e.retryable)])}
        unprocessed = deepcopy(n05.data.unprocessed_criteria)
        if not n05.data.criteria:
            return {"result": CriteriaReviewResult(request_ref, "success", n05.outcome, [], unprocessed, False, [])}
        errors, findings, safe = [], [], bool(unprocessed)
        incomplete, partial = False, n05.status == "partial"
        for record in state["records"]:
            c = record["criterion"]
            for name in ("n01", "n02", "n06", "n03"):
                r = record.get(name)
                if r is None: continue
                partial |= r.status == "partial"
                if r.status != "success":
                    e = _error_from("N-" + name[1:], r)
                    errors.append(CriterionExecutionError(c.criterion_id, e.node, e.error_code, e.retryable))
                if name in ("n01", "n02"): incomplete |= r.status != "success"
            n01, n02, n06, n03 = (record.get(name) for name in ("n01", "n02", "n06", "n03"))
            bundle = n02.data if n02 is not None and n02.status != "error" else None
            sources = {e.chunk_id: e for e in bundle.evidence_items} if bundle else {}
            incomplete |= any(e.context_truncated for e in sources.values())
            proposals = {f.condition_id: f for f in n06.data.conditions} if n06 and n06.status != "error" else {}
            checks = {check.claim_id: check for check in n03.data.checks} if n03 and n03.status != "error" else {}
            if n01.status != "error" and not n01.data.candidates: safe = True
            if n02 and n02.status != "error" and not n02.data.evidence_items: safe = True
            reviewed = []
            for condition in c.conditions:
                proposal = proposals.get(condition.condition_id); claim_id = claim_id_for(c, condition)
                verdict = CriterionVerdict.UNDETERMINED
                proposed = proposal.verdict if proposal else verdict; check = checks.get(claim_id)
                if n01.status == "error":
                    reason = "근거 검색 실행에 실패해 기준을 판정하지 못했다."
                elif not n01.data.candidates:
                    reason = "검색 범위에서 관련 근거를 확보하지 못했다. 문서 내 정보 부재를 증명하지는 않는다."
                elif n02 is not None and n02.status == "error":
                    reason = "원문 문맥 조회 실행에 실패해 기준을 판정하지 못했다."
                elif bundle is not None and not bundle.evidence_items:
                    reason = "판정에 사용할 원문 문맥을 확보하지 못했다."
                else:
                    reason = "기준 판정 실행을 완료하지 못했다."
                verified_text = None; refs = list(proposal.evidence_refs) if proposal else []
                if proposal:
                    if proposed == CriterionVerdict.UNDETERMINED: reason, safe = proposal.reason, True
                    elif check is not None:
                        safe = True; reason = check.reason
                        if check.verdict == Verdict.SUPPORTED: verdict, verified_text = proposed, proposal.claim_text
                        else: reason = "판정과 근거 연결을 확인하지 못했다: " + check.reason
                    else: reason = "N-03 근거 검증 실행을 완료하지 못해 판정을 보류했다."
                reviewed.append(ReviewedCondition(condition.condition_id, claim_id, verdict, proposed,
                    reason, verified_text, refs, deepcopy(proposal.quotes) if proposal else [], [EvidenceSource(s.chunk_id, s.document_id, s.document_version,
                        deepcopy(s.source_refs), s.context_truncated) for s in (sources[ref] for ref in refs)], deepcopy(check)))
            findings.append(ReviewedCriterion(c.criterion_id, c.input_index, c.original_text, c.operator,
                                              aggregate(c.operator, [v.verdict for v in reviewed]), reviewed))
        status = "error" if errors and not safe else "partial" if errors or partial else "success"
        outcome = None
        if status != "error":
            outcome = ("partial_criteria" if unprocessed else "insufficient_evidence"
                       if any(f.verdict == CriterionVerdict.UNDETERMINED for f in findings) else None)
        return {"result": CriteriaReviewResult(request_ref, status, outcome, findings, unprocessed, incomplete, errors)}


def main(argv=None):
    p = argparse.ArgumentParser(description="T-02 명시적 기준 충족 검토")
    p.add_argument("--chunks", type=Path, required=True); p.add_argument("--blocks", type=Path, required=True)
    criteria = p.add_mutually_exclusive_group()
    criteria.add_argument("--criterion", action="append", help="독립 기준마다 반복 지정")
    criteria.add_argument("--criteria-file", type=Path, help="기준 문자열 목록 JSON")
    p.add_argument("--query", default="사용자가 명시한 기준을 문서 근거로 검토해줘.")
    p.add_argument("--request-ref", required=True)
    p.add_argument("--scope", nargs=2, action="append", required=True, metavar=("DOCUMENT_ID", "VERSION"))
    p.add_argument("--model", required=True); p.add_argument("--provider", choices=("local", "cloud"), default="local")
    p.add_argument("--base-url"); p.add_argument("--timeout", type=float, default=120); p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--num-ctx", type=int, default=16384, help="로컬 모델 컨텍스트 토큰 설정")
    p.add_argument("--trace-out", type=Path, help="명시할 때만 원문 포함 개발 실행 기록 저장")
    a = p.parse_args(argv)
    try:
        texts = json.loads(a.criteria_file.expanduser().read_text(encoding="utf-8")) if a.criteria_file else a.criterion or []
        chunks, blocks = load_chunks(a.chunks), load_block_lookup(a.blocks)
        base = a.base_url if a.base_url is not None else ("https://ollama.com" if a.provider == "cloud" else "http://localhost:11434")
        client = OllamaClient(a.model, base, provider=a.provider, timeout=a.timeout, num_ctx=a.num_ctx,
                              api_key=os.environ.get("OLLAMA_API_KEY") if a.provider == "cloud" else None)
        w = CriteriaReviewWorkflow(BM25Retriever(chunks), build_chunk_lookup(chunks), blocks, client, top_k=a.top_k)
        task = CriteriaReviewTask(a.request_ref, a.query, texts, [DocumentRef(*d) for d in a.scope])
        state = w.run_with_state(task); result = state["result"]
        if a.trace_out:
            a.trace_out.expanduser().write_text(json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False,
                default=lambda value: asdict(value) if is_dataclass(value) else value), encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError):
        result = CriteriaReviewResult(a.request_ref, "error", None, [], [], False,
                                      [CriterionExecutionError(None, "T-02", "INVALID_CRITERIA_FILES_OR_CONFIG", False)])
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, allow_nan=False))
    return 2 if result.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
