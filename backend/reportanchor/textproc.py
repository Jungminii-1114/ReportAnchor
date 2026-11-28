"""BM25용 토큰화. Kiwi 형태소 분석을 우선 쓰고, 설치되지 않았으면 안전한 정규식 폴백을 쓴다.

색인과 질의가 같은 토큰화를 쓰도록 `tokenizer_id()`를 청크와 함께 기록한다.
"""

from __future__ import annotations

import re
import threading

_lock = threading.Lock()
_kiwi = None
_kiwi_failed = False

_KIWI_KEEP_PREFIX = ("NN", "SL", "SH", "XR", "VV", "VA", "NR", "NP")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_PUNCT_RE = re.compile(r"[^\w]+")
_WORD_RE = re.compile(r"[가-힣]+|[A-Za-z]+|\d+(?:[.,]\d+)*")


def _get_kiwi():
    global _kiwi, _kiwi_failed
    if _kiwi is not None or _kiwi_failed:
        return _kiwi
    with _lock:
        if _kiwi is None and not _kiwi_failed:
            try:
                from kiwipiepy import Kiwi

                _kiwi = Kiwi()
            except Exception:  # 설치·모델 로드 실패 시 폴백
                _kiwi_failed = True
    return _kiwi


def tokenizer_id() -> str:
    if _get_kiwi() is not None:
        import kiwipiepy

        return f"kiwi-{kiwipiepy.__version__}"
    return "fallback-v1"


def _norm_number(tok: str) -> str:
    return tok.replace(",", "")


def _fallback(text: str) -> list[str]:
    out: list[str] = []
    for w in _WORD_RE.findall(text):
        if w[0].isdigit():
            out.append(_norm_number(w))
        elif "가" <= w[0] <= "힣":
            out.append(w)
            # 조사가 붙은 어절(예: '운영비는')도 부분 일치하도록 2글자 n-gram을 더한다.
            out.extend(w[i : i + 2] for i in range(len(w) - 1))
        else:
            out.append(w.lower())
    return out


def tokenize(text: str) -> list[str]:
    kiwi = _get_kiwi()
    if kiwi is None:
        return _fallback(text)
    toks: list[str] = []
    for t in kiwi.tokenize(text):
        # 의존 명사(NNB: '년'·'원' 등)는 거의 모든 청크에 나와 순위를 흐리므로 제외한다.
        if t.tag.startswith(_KIWI_KEEP_PREFIX) and t.tag != "NNB" and t.tag != "SN":
            form = _PUNCT_RE.sub("", t.form.lower())
            if form:
                toks.append(form)
    # 숫자는 Kiwi 분할과 무관하게 쉼표를 제거한 형태로 통일한다('4,532' == '4532').
    toks.extend(_norm_number(n) for n in _NUMBER_RE.findall(text))
    return toks
