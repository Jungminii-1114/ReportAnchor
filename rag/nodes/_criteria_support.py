"""T-02 전용 공통 검사. 검색/판정/재시도 정책을 실행하지 않는 내부 함수들이다."""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError

from rag.contracts import (
    Criterion, CriterionVerdict, EvidenceBundle, NodeResult, ReviewDraft, StepDiagnostic,
)
from rag.nodes.verify_grounding import VerifyGroundingNode, _validate_client_config

MAX_CRITERIA = 20
MAX_CONDITIONS = 8
MAX_CRITERION_CHARS = 4000


def text(value):
    return isinstance(value, str) and bool(value.strip())


def strict_json(raw):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("중복 JSON 키")
            obj[key] = value
        return obj
    def reject_constant(_):
        raise ValueError("비유한 JSON 값")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)


def exact_keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("출력 필드 불일치")


def failure(step, code, reason, retryable=False):
    return NodeResult("error", None, {step: StepDiagnostic(
        "failed", error_code=code, reason=reason, retryable=retryable,
    )}, None)


def model_failure(exc):
    if isinstance(exc, HTTPError):
        return "MODEL_HTTP_ERROR", exc.code in (408, 429) or 500 <= exc.code < 600
    if isinstance(exc, (TimeoutError, URLError, ConnectionError, OSError)):
        return "MODEL_CONNECTION_ERROR", True
    return "MODEL_RESPONSE_ERROR", False


def validate_criterion(criterion):
    if (not isinstance(criterion, Criterion) or not text(criterion.criterion_id)
            or type(criterion.input_index) is not int or criterion.input_index < 0
            or not text(criterion.original_text) or len(criterion.original_text) > MAX_CRITERION_CHARS
            or criterion.operator not in ("all", "any")
            or not isinstance(criterion.conditions, list)
            or not 1 <= len(criterion.conditions) <= MAX_CONDITIONS):
        raise ValueError("기준 계약 오류")
    from rag.contracts import CriterionCondition
    ids = set()
    for condition in criterion.conditions:
        if (not isinstance(condition, CriterionCondition) or not text(condition.condition_id)
                or condition.condition_id in ids or not text(condition.text)
                or not text(condition.source_quote)
                or condition.source_quote not in criterion.original_text):
            raise ValueError("조건 계약 오류")
        ids.add(condition.condition_id)


def validate_bundle(client, request_ref, bundle):
    VerifyGroundingNode(client)._validate_input(request_ref, ReviewDraft(request_ref, [], []), bundle)
    # 좌표는 모델에 보내지 않아도 반환 계약에서 비유한 JSON 값을 허용하지 않는다.
    json.dumps([item.source_refs for item in bundle.evidence_items], allow_nan=False)


def aggregate(operator, verdicts):
    """평면 AND/OR의 3값 논리. 근거 부족을 부정으로 바꾸지 않는다."""
    values = list(verdicts)
    if operator not in ("all", "any") or not values:
        raise ValueError("집계할 조건/연산자 오류")
    if any(not isinstance(value, CriterionVerdict) for value in values):
        raise TypeError("판정 enum 필요")
    met, no, unknown = CriterionVerdict.MET, CriterionVerdict.NOT_MET, CriterionVerdict.UNDETERMINED
    if operator == "all":
        return no if no in values else unknown if unknown in values else met
    return met if met in values else unknown if unknown in values else no


def explicit_unsupported_logic(original):
    """명백한 기호/영문 혼합 논리만 보수적으로 차단한다. 의미 검증기는 아니다."""
    and_token = re.search(r"\bAND\b|&&|∧", original, re.I)
    or_token = re.search(r"\bOR\b|\|\||∨", original, re.I)
    unsupported_korean_group = (
        re.search(r"\([^)]*(?:또는|혹은)[^)]*\)", original)
        and re.search(r"그리고|모두|및|와|과", re.sub(r"\([^)]*\)", "", original))
    ) or (
        re.search(r"\([^)]*(?:그리고|모두|및|와|과)[^)]*\)", original)
        and re.search(r"또는|혹은", re.sub(r"\([^)]*\)", "", original))
    )
    return bool((and_token and or_token) or re.search(r"\bNOT\b|¬", original, re.I)
                or unsupported_korean_group)


MODEL_ERRORS = (HTTPError, URLError, TimeoutError, ConnectionError, OSError, ValueError, TypeError, KeyError)
