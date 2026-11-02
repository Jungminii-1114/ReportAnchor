"""
BM25 검색에 사용하는 한국어 토큰화 정책

rag_data.py가 만든 청크에서 검색용 문자열을 꺼내 tokenizer.py로 넘겨야 함.
- chunks.jsonl에서 읽기
- 각 청크에서 ["search_text"] 추출 -> 문자열 목록을 전달

{
    "chunk_id": "doc_0530d100e08517b6:c_48b8752fe3f1375f4ddb",
    "pdf_pages": [1],
    "search_text": "2025년도 사업계획 및 예산(안)",
    # 원문·문서 버전·블록 참조 등 다른 필드도 있음
}
"""

import re
import unicodedata

from kiwipiepy import Kiwi

TOKENIZER_VERSION="kiwi-bm25-v1"

kiwi = Kiwi()

def normalize_text(text: str) -> str:
    """
    검색용 문자열을 일관된 형태로 정규화한다.
    - 문자열인지 확인
        - Unicode NFKC 정규화
        - 영문 소문자화
        - 연속 공백을 하나로 바꾸고 양끝 공백 제거
    """
    if not isinstance(text, str):
        raise TypeError("text는 문자열이어야 합니다.")

    normalized = unicodedata.normalize("NFKC", text)  # Unicode 정규화: 다른 문자 표현을 일정한 형태로 맞춘다.
    normalized = normalized.lower()                   # 소문자화
    normalized = re.sub(r"\s+", " ", normalized)      # 연속 공백 정리
    normalized = normalized.strip()                   # 양끝 공백 제거

    return normalized

EXCLUDED_TAGS = {"SF", "SP", "SS", "SSO", "SSC", "SE"}

def keep_token(token) -> bool:
    """
    검색에 사용할 형태소인지 파악하는 함수
    keep_token(): 문자열이 아니라 Kiwi 토큰 객체를 받는다.
    """
    if token.tag.startswith(("J", "E")):
        return False
    elif token.tag in EXCLUDED_TAGS:
        return False
    else:
        return True

def tokenize(text: str) -> list[str]:
    """
    tokenize()는 문자열 목록 전체가 아닌, 문자열 '하나'만 받는다.
    여러 청크를 처리하는 반복문은 호출하는 쪽에서 처리하기

    normalize_text(), keep_token()을 연결하는 역할.
    """
    result = [] # Result

    normalized_text = normalize_text(text)
    if normalized_text == "":
        return []

    for token in kiwi.tokenize(normalized_text):
        if keep_token(token) is False:
            continue
        else:
            result.append(token.form)

    return result

if __name__ == "__main__":
    sample = "２０２５년   ETRI\n예산은 23.6% 증가했다."

    print("정규화:", normalize_text(sample))
    print(
        "형태소:",
        [(token.form, token.tag)
         for token in kiwi.tokenize(normalize_text(sample))]
    )
    print("검색 토큰:", tokenize(sample))