"""생성·검증 노드의 모델 응답 계약. 출처·의미 검증은 각 노드에서 계속 수행한다."""


def draft_response_schema(evidence_refs: list[str], claim_ids: list[str] | None = None) -> dict:
    """초안은 코드를 통해 ID를 받고, 보정 응답은 기존 대상 ID만 사용할 수 있다."""
    properties = {
        "claim_text": {"type": "string", "minLength": 1},
        "evidence_refs": {"type": "array", "items": {"type": "string", "enum": list(dict.fromkeys(evidence_refs))}},
    }
    required = ["claim_text", "evidence_refs"]
    if claim_ids is not None:
        properties["claim_id"] = {"type": "string", "enum": list(dict.fromkeys(claim_ids))}
        required.append("claim_id")
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "claims": {"type": "array", "items": {"type": "object", "properties": properties,
                        "required": required, "additionalProperties": False}},
            "unanswered_items": {"type": "array", "items": {"type": "string", "minLength": 1}},
        },
        "required": ["claims", "unanswered_items"],
    }


def verification_response_schema(claim_id: str | None = None) -> dict:
    """주장 ID 일치와 근거 연결은 응답을 받은 N-03 코드가 별도로 검사한다."""
    claim_property = {"type": "string", "minLength": 1}
    if claim_id is not None:
        claim_property["enum"] = [claim_id]
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "claim_id": claim_property,
            "verdict": {"type": "string", "enum": ["supported", "contradicted", "insufficient"]},
            "reason": {"type": "string", "minLength": 1},
        },
        "required": ["claim_id", "verdict", "reason"],
    }


def criteria_response_schema(count: int) -> dict:
    """원문 입력별 정확한 대응은 N-05 Python 검사가 확인한다."""
    return {
        "type": "object", "additionalProperties": False, "required": ["items"],
        "properties": {"items": {"type": "array", "minItems": count, "maxItems": count,
            "items": {"type": "object", "additionalProperties": False,
                "required": ["input_index", "status", "operator", "conditions", "reason"],
                "properties": {
                    "input_index": {"type": "integer", "enum": list(range(count))},
                    "status": {"type": "string", "enum": ["structured", "unsupported_structure", "needs_clarification"]},
                    "operator": {"type": ["string", "null"], "enum": ["all", "any", None]},
                    "reason": {"type": ["string", "null"]},
                    "conditions": {"type": "array", "maxItems": 8, "items": {
                        "type": "object", "additionalProperties": False, "required": ["text", "source_quote"],
                        "properties": {"text": {"type": "string", "minLength": 1},
                                       "source_quote": {"type": "string", "minLength": 1}}}},
                }}}},
    }


def criterion_response_schema(condition_ids: list[str], quote_ids: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False, "required": ["conditions"],
        "properties": {"conditions": {"type": "array", "minItems": len(condition_ids),
            "maxItems": len(condition_ids), "items": {"type": "object", "additionalProperties": False,
                "required": ["condition_id", "verdict", "claim_text", "reason", "quote_ids"],
                "properties": {
                    "condition_id": {"type": "string", "enum": condition_ids},
                    "verdict": {"type": "string", "enum": ["met", "not_met", "undetermined"]},
                    "claim_text": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                    "quote_ids": {"type": "array", "uniqueItems": True,
                                  "items": {"type": "string", "enum": quote_ids}},
                }}}},
    }
