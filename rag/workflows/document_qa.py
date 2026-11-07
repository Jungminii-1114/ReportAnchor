"""T-01 Document QA: N-01 → N-02 → N-04 → N-03, 모순 주장만 1회 보정.

LangGraph가 고정 분기와 실행 상태를 관리한다. 검색·원문 조회는 한 번만 하며,
동일 근거와 원래 질문으로 N-04/N-03을 최대 한 번 더 호출한다. 결과는 메인
에이전트용 WorkflowResult이다. N-10 권한 판단·N-11 최종 답변 작성은 포함하지 않는다.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from rag.contracts import (
    ClaimVerification, ContextExpansionConfig, DocumentRef, EvidenceBundle, EvidenceSource,
    NodeResult, RetrievalCandidates, RetrievalRequest, RevisionTarget, ReviewDraft, RoutedTask,
    StepDiagnostic, UnconfirmedItem, VerificationReport, VerifiedClaim, Verdict, WorkflowError,
    WorkflowResult,
)
from rag.nodes.draft_answer import DraftAnswerNode
from rag.nodes.get_source_context import build_chunk_lookup, get_source_context, load_block_lookup
from rag.nodes.retrieve_evidence import retrieve_evidence
from rag.nodes.verify_grounding import VerifyGroundingNode
from rag.retrieval.bm25 import BM25Retriever, load_chunks


class DocumentQAState(TypedDict, total=False):
    task: RoutedTask
    validation_error: WorkflowError
    n01: NodeResult[RetrievalCandidates]
    n02: NodeResult[EvidenceBundle]
    n04: NodeResult[ReviewDraft]
    n03: NodeResult[VerificationReport]
    n04_revision: NodeResult[ReviewDraft]
    n03_recheck: NodeResult[VerificationReport]
    revision_targets: list[RevisionTarget]
    revision_attempted: bool
    result: WorkflowResult


def _contract_error() -> NodeResult:
    return NodeResult("error", None, {"workflow_contract": StepDiagnostic(
        "failed", error_code="NODE_CONTRACT_VIOLATION",
        reason="노드 반환 자료형·요청 ID·참조 무결성을 확인하세요.", retryable=False,
    )}, None)


def _checked(result, data_type, request_ref):
    if not isinstance(result, NodeResult):
        return _contract_error()
    if result.status == "error":
        return result if result.data is None else _contract_error()
    if (result.status not in ("success", "partial") or not isinstance(result.data, data_type)
            or result.data.request_ref != request_ref):
        return _contract_error()
    return result


def _error_from(node: str, result: NodeResult) -> WorkflowError:
    ordered = ([result.diagnostics["verify_claims"]] if "verify_claims" in result.diagnostics else [])
    ordered += [value for key, value in result.diagnostics.items() if key != "verify_claims"]
    for diagnostic in ordered:
        if diagnostic.status == "failed":
            if diagnostic.failed_items:
                item = diagnostic.failed_items[0]
                return WorkflowError(node, item.error_code, item.retryable)
            return WorkflowError(node, diagnostic.error_code or "NODE_EXECUTION_FAILED",
                                 diagnostic.retryable is True)
    return WorkflowError(node, "NODE_EXECUTION_FAILED", False)


class DocumentQAWorkflow:
    """BM25 기준선과 기존 Small Node들을 연결하는 재사용 가능한 T-01 tool이다."""

    def __init__(self, retriever: BM25Retriever, chunk_lookup: dict, block_lookup: dict,
                 draft_node: DraftAnswerNode, *, verifier: VerifyGroundingNode | None = None,
                 top_k: int = 3, retrieval_profile: str = "bm25-baseline-v1",
                 context_profiles: dict[str, ContextExpansionConfig] | None = None):
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k는 양의 정수여야 합니다.")
        self.retriever = retriever
        self.chunk_lookup = chunk_lookup
        self.block_lookup = block_lookup
        self.draft_node = draft_node
        self.verifier = verifier if verifier is not None else VerifyGroundingNode(draft_node.client)
        self.top_k = top_k
        self.retrieval_profile = retrieval_profile
        self.context_profiles = (dict(context_profiles) if context_profiles is not None
                                 else {"fixed-context-v1": ContextExpansionConfig()})
        if (not isinstance(retrieval_profile, str) or not retrieval_profile.strip()
                or any(not isinstance(key, str) or not key.strip()
                       or not isinstance(value, ContextExpansionConfig)
                       for key, value in self.context_profiles.items())):
            raise ValueError("검색·문맥 프로필 설정을 확인하세요.")
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(DocumentQAState)
        graph.add_node("validate_input", self._validate)
        graph.add_node("N-01", self._retrieve)
        graph.add_node("N-02", self._context)
        graph.add_node("N-04", self._draft)
        graph.add_node("N-03", self._verify)
        graph.add_node("assemble_result", self._assemble)
        graph.add_edge(START, "validate_input")
        graph.add_conditional_edges("validate_input", lambda s: "assemble_result" if "validation_error" in s else "N-01")
        graph.add_conditional_edges("N-01", lambda s: "N-02" if s["n01"].status != "error" and s["n01"].data.candidates else "assemble_result")
        graph.add_conditional_edges("N-02", lambda s: "N-04" if s["n02"].status != "error" and s["n02"].data.evidence_items else "assemble_result")
        graph.add_conditional_edges("N-04", self._after_draft)
        graph.add_conditional_edges("N-03", lambda s: "N-04" if not s.get("revision_attempted", False) and s.get("revision_targets") else "assemble_result")
        graph.add_edge("assemble_result", END)
        return graph.compile(name="T-01 Document QA")

    def run(self, task: RoutedTask) -> WorkflowResult:
        return self.run_with_state(task)["result"]

    def run_with_state(self, task: RoutedTask) -> DocumentQAState:
        """호출한 노드의 NodeResult만 남긴다. 새 호출은 새 상태로 시작한다.

        하네스는 반환 상태를 필요할 때만 아티팩트로 저장할 수 있다. API 키·모델의
        원시 응답은 상태에 넣지 않는다. 영속 Checkpointer/H-02 구현은 포함하지 않는다.
        """
        return self.graph.invoke({"task": deepcopy(task), "revision_attempted": False},
                                 config={"recursion_limit": 16})

    def _validate(self, state):
        task = state["task"]
        valid = isinstance(task, RoutedTask)
        if valid:
            valid = (all(isinstance(v, str) and v.strip() for v in
                         (task.request_ref, task.question, task.retrieval_profile, task.context_profile))
                     and isinstance(task.document_scope, list)
                     and all(isinstance(ref, DocumentRef) for ref in task.document_scope)
                     and task.retrieval_profile == self.retrieval_profile
                     and task.context_profile in self.context_profiles)
        if valid:
            try:
                for ref in task.document_scope:
                    ref.__post_init__()
            except (TypeError, ValueError):
                valid = False
        return {} if valid else {"validation_error": WorkflowError("T-01", "INVALID_ROUTED_TASK", False)}

    def _retrieve(self, state):
        task = state["task"]
        result = retrieve_evidence(RetrievalRequest(task.request_ref, task.question,
            task.document_scope, task.retrieval_profile), self.retriever, top_k=self.top_k)
        result = _checked(result, RetrievalCandidates, task.request_ref)
        if result.status != "error" and not isinstance(result.data.candidates, list):
            result = _contract_error()
        return {"n01": result}

    def _context(self, state):
        task = state["task"]
        result = get_source_context(state["n01"].data, self.chunk_lookup, self.block_lookup,
            document_scope=task.document_scope, config=self.context_profiles[task.context_profile],
            context_profile_ref=task.context_profile)
        result = _checked(result, EvidenceBundle, task.request_ref)
        if result.status != "error":
            allowed = {(ref.document_id, ref.document_version) for ref in task.document_scope}
            try:
                # N-03의 구조 검사를 재사용하며 모델은 호출하지 않는다.
                self.verifier._validate_input(task.request_ref, ReviewDraft(task.request_ref, [], []), result.data)
                if result.data.context_profile_ref != task.context_profile:
                    raise ValueError("context profile mismatch")
                if any((item.document_id, item.document_version) not in allowed for item in result.data.evidence_items):
                    raise ValueError("out of scope")
            except (TypeError, ValueError):
                result = _contract_error()
        return {"n02": result}

    def _draft(self, state):
        task = state["task"]
        revision = "n03" in state
        targets = state.get("revision_targets") if revision else None
        result = self.draft_node.run(task.request_ref, task.question, state["n02"].data,
                                     revision_targets=targets)
        result = _checked(result, ReviewDraft, task.request_ref)
        if result.status != "error":
            try:
                self.verifier._validate_input(task.request_ref, result.data, state["n02"].data)
                allowed = {item.chunk_id for item in state["n02"].data.evidence_items}
                target_ids = {t.claim_id for t in targets} if targets is not None else None
                for claim in result.data.claims:
                    if not set(claim.evidence_refs) <= allowed:
                        raise ValueError("unknown reference")
                    if revision and (claim.claim_id not in target_ids or not claim.evidence_refs):
                        raise ValueError("invalid corrected claim")
                if revision and result.data.unanswered_items:
                    raise ValueError("invalid revision unanswered items")
                if result.data.claims and not any(c.evidence_refs for c in result.data.claims):
                    raise ValueError("all unreferenced")
            except (TypeError, ValueError):
                result = _contract_error()
        return ({"n04_revision": result, "revision_attempted": True} if revision else {"n04": result})

    @staticmethod
    def _after_draft(state):
        key = "n04_revision" if state.get("revision_attempted", False) else "n04"
        result = state[key]
        return "N-03" if result.status != "error" and any(c.evidence_refs for c in result.data.claims) else "assemble_result"

    def _verify(self, state):
        revision = state.get("revision_attempted", False)
        draft = state["n04_revision" if revision else "n04"].data
        result = _checked(self.verifier.run(state["task"].request_ref, draft, state["n02"].data),
                          VerificationReport, state["task"].request_ref)
        if result.status != "error":
            try:
                claims = {claim.claim_id: claim for claim in draft.claims}
                seen = set()
                for check in result.data.checks:
                    if (not isinstance(check, ClaimVerification) or check.claim_id in seen
                            or check.claim_id not in claims or not isinstance(check.verdict, Verdict)
                            or not isinstance(check.reason, str) or not check.reason.strip()
                            or not check.evidence_refs
                            or set(check.evidence_refs) != set(claims[check.claim_id].evidence_refs)):
                        raise ValueError("invalid check")
                    seen.add(check.claim_id)
                completed = seen
                failed = {item.ref for diagnostic in result.diagnostics.values()
                          for item in diagnostic.failed_items if diagnostic.status == "failed"
                          and item.error_code != "NUMERIC_HINTS_FAILED"}
                eligible = {c.claim_id for c in draft.claims if c.evidence_refs}
                if completed & failed or completed | failed != eligible:
                    raise ValueError("incomplete or conflicting checks")
                if result.status == "success" and failed:
                    raise ValueError("status mismatch")
            except (TypeError, ValueError, AttributeError):
                result = _contract_error()
        if revision:
            return {"n03_recheck": result}
        targets = []
        if result.status != "error":
            claims = {claim.claim_id: claim for claim in draft.claims}
            targets = [RevisionTarget(check.claim_id, claims[check.claim_id].claim_text,
                check.reason, list(check.evidence_refs)) for check in result.data.checks
                if check.verdict == Verdict.CONTRADICTED]
        return {"n03": result, "revision_targets": targets}

    @staticmethod
    def _evidence_incomplete(state):
        n01 = state.get("n01")
        if n01 and any(d.status == "failed" and any(name in key.lower() for name in ("bm25", "dense"))
                       for key, d in n01.diagnostics.items()):
            return True
        n02 = state.get("n02")
        return bool(n02 and (n02.status == "partial" or any(
            d.excluded_refs for key, d in n02.diagnostics.items() if key == "context_budget")))

    def _assemble(self, state):
        task = state["task"]
        request_ref = task.request_ref if isinstance(task, RoutedTask) and isinstance(task.request_ref, str) else ""
        fatal = state.get("validation_error")
        called = [("N-01", state.get("n01")), ("N-02", state.get("n02")),
                  ("N-04", state.get("n04")), ("N-03", state.get("n03")),
                  ("N-04", state.get("n04_revision")), ("N-03", state.get("n03_recheck"))]
        if fatal is None:
            fatal = next((_error_from(name, result) for name, result in called[:4]
                          if result is not None and result.status == "error"), None)
        verified, unconfirmed = [], []
        draft_result = state.get("n04")
        if draft_result is not None and draft_result.status != "error":
            draft = draft_result.data
            unconfirmed.extend(UnconfirmedItem("N-04", "unanswered_item", text)
                               for text in draft.unanswered_items)
            checks = {c.claim_id: c for c in state["n03"].data.checks} if state.get("n03") and state["n03"].status != "error" else {}
            revision = state.get("n04_revision")
            corrections = ({c.claim_id: c for c in revision.data.claims}
                           if revision is not None and revision.status != "error" else {})
            recheck = state.get("n03_recheck")
            rechecks = ({c.claim_id: c for c in recheck.data.checks}
                        if recheck is not None and recheck.status != "error" else {})
            sources = {item.chunk_id: item for item in state["n02"].data.evidence_items}
            for original in draft.claims:
                claim, check = original, checks.get(original.claim_id)
                if original.claim_id in corrections:
                    claim, check = corrections[original.claim_id], rechecks.get(original.claim_id)
                if not claim.evidence_refs:
                    unconfirmed.append(UnconfirmedItem("N-04", "no_evidence_ref",
                        "원문 근거 참조가 없어 검증하지 않았다.", claim.claim_id, claim.claim_text))
                elif check is None:
                    unconfirmed.append(UnconfirmedItem("N-03", "verification_failed",
                        "확보한 자료에 대한 검증 실행을 완료하지 못했다.", claim.claim_id,
                        claim.claim_text, list(claim.evidence_refs)))
                elif check.verdict == Verdict.SUPPORTED:
                    verified.append(VerifiedClaim(claim.claim_id, claim.claim_text, check.verdict,
                        check.reason, list(check.evidence_refs), [EvidenceSource(item.chunk_id,
                        item.document_id, item.document_version, deepcopy(item.source_refs), item.context_truncated)
                        for item in (sources[ref] for ref in check.evidence_refs)]))
                else:
                    # 반박된 문장은 출력하지 않는다. 근거 부족 판정은 문장·사유를 보존한다.
                    code = check.verdict.value
                    reason = check.reason
                    if check.verdict == Verdict.CONTRADICTED:
                        if revision is not None and revision.status == "error":
                            code, reason = "revision_failed", "자료와 충돌하는 주장의 보정 실행에 실패했다."
                        elif state.get("revision_attempted") and original.claim_id not in corrections:
                            code, reason = "revision_unavailable", "자료와 충돌하는 주장을 확보한 근거로 보정하지 못했다."
                    unconfirmed.append(UnconfirmedItem("N-03", code, reason, claim.claim_id,
                        None if check.verdict == Verdict.CONTRADICTED else claim.claim_text,
                        list(check.evidence_refs)))
        revision_error = next((_error_from(name, result) for name, result in called[4:]
                               if result is not None and result.status == "error"), None)
        if fatal is None and revision_error is not None and not verified:
            fatal = revision_error
        if fatal:
            result = WorkflowResult(request_ref, "error", None, [], unconfirmed, False, fatal)
        else:
            partial = any(result is not None and result.status != "success" for _, result in called)
            outcome = "insufficient_evidence" if not verified else ("partial_answer" if unconfirmed else None)
            result = WorkflowResult(request_ref, "partial" if partial else "success", outcome,
                                    verified, unconfirmed, self._evidence_incomplete(state), None)
        return {"result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T-01 문서 질의응답과 1회 보정·재검증을 실행한다.")
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--blocks", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--request-ref", required=True)
    parser.add_argument("--scope", nargs=2, action="append", metavar=("DOCUMENT_ID", "VERSION"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", choices=("local", "cloud"), default="local")
    parser.add_argument("--base-url")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--max-context-chars", type=int, default=12000)
    args = parser.parse_args(argv)
    try:
        chunks = load_chunks(args.chunks)
        blocks = load_block_lookup(args.blocks)
        generator = DraftAnswerNode(args.model, args.base_url if args.base_url is not None else (
            "https://ollama.com" if args.provider == "cloud" else "http://localhost:11434"),
            provider=args.provider, api_key=os.environ.get("OLLAMA_API_KEY") if args.provider == "cloud" else None,
            timeout=args.timeout)
        workflow = DocumentQAWorkflow(BM25Retriever(chunks), build_chunk_lookup(chunks), blocks,
            generator, top_k=args.top_k,
            context_profiles={"fixed-context-v1": ContextExpansionConfig(max_total_chars=args.max_context_chars)})
        task = RoutedTask(args.request_ref, args.query, [DocumentRef(*pair) for pair in args.scope],
                          "bm25-baseline-v1", "fixed-context-v1")
    except (OSError, TypeError, ValueError, KeyError):
        result = WorkflowResult(args.request_ref, "error", None, [], [], False,
                                WorkflowError("T-01", "INVALID_WORKFLOW_INPUT", False))
    else:
        result = workflow.run(task)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, allow_nan=False))
    return 2 if result.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
