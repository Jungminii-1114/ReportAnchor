"""PyMuPDF 파싱·구조 청킹·페이지 렌더링.

`parse_pdf`는 데몬 본체와 격리된 자식 프로세스(ProcessPoolExecutor)에서 실행된다.
오프셋은 각 블록 `raw_content`의 유니코드 코드 포인트 인덱스 `[start, end)`다.
"""

from __future__ import annotations

from typing import Any

MAX_CHUNK_CHARS = 900
HEADING_MAX_CHARS = 40
MAX_PAGES = 2000
MAX_RENDER_PX = 4096
HIGHLIGHT_RGB = (0x1F / 255, 0x6B / 255, 0x54 / 255)  # #1F6B54


class ParseError(Exception):
    """사용자에게 보여도 안전한 메시지만 담는다."""


def _normalize_block_text(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def _split_long(text: str, limit: int) -> list[tuple[int, int]]:
    """긴 블록을 공백 경계 근처에서 [start, end) 구간으로 나눈다."""
    spans: list[tuple[int, int]] = []
    start, n = 0, len(text)
    while start < n:
        end = min(n, start + limit)
        if end < n:
            cut = max(text.rfind(" ", start + limit // 2, end), text.rfind("\n", start + limit // 2, end))
            if cut > start:
                end = cut + 1
        spans.append((start, end))
        start = end
    return spans


def chunk_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """페이지·블록 경계를 지키는 구조 기반 청킹. 짧은 단일 행 블록(제목 추정)은 새 청크를 시작한다."""
    chunks: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    cur_len = 0
    cur_page: int | None = None

    def flush() -> None:
        nonlocal cur, cur_len
        if cur:
            text = "\n".join(blocks[s["block_seq"]]["raw_content"][s["start"] : s["end"]] for s in cur)
            chunks.append({"seq": len(chunks), "pages": [cur_page], "spans": cur, "text": text})
        cur, cur_len = [], 0

    for b in blocks:
        raw = b["raw_content"]
        if b["page"] != cur_page:
            flush()
            cur_page = b["page"]
        is_heading = len(raw) <= HEADING_MAX_CHARS and "\n" not in raw
        if len(raw) > MAX_CHUNK_CHARS:
            flush()
            for s, e in _split_long(raw, MAX_CHUNK_CHARS):
                cur = [{"block_seq": b["seq"], "start": s, "end": e}]
                flush()
            continue
        if cur and (cur_len + len(raw) > MAX_CHUNK_CHARS or is_heading):
            flush()
        cur.append({"block_seq": b["seq"], "start": 0, "end": len(raw)})
        cur_len += len(raw) + 1
    flush()
    return chunks


def parse_pdf(path: str) -> dict[str, Any]:
    """자식 프로세스 진입점. 반환값은 피클 가능한 기본 자료형만 쓴다."""
    import pymupdf

    from .textproc import tokenize, tokenizer_id

    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001
        raise ParseError("PDF를 열 수 없습니다.") from exc
    with doc:
        if doc.needs_pass:
            raise ParseError("암호가 걸린 PDF는 처리할 수 없습니다.")
        if doc.page_count == 0:
            raise ParseError("페이지가 없는 PDF입니다.")
        if doc.page_count > MAX_PAGES:
            raise ParseError(f"{MAX_PAGES}쪽을 넘는 PDF는 처리할 수 없습니다.")
        blocks: list[dict[str, Any]] = []
        for pno, page in enumerate(doc, start=1):
            for x0, y0, x1, y1, text, _bno, btype in page.get_text("blocks", sort=True):
                if btype != 0:
                    continue
                raw = _normalize_block_text(text)
                if not raw.strip():
                    continue
                blocks.append(
                    {
                        "seq": len(blocks),
                        "page": pno,
                        "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                        "raw_content": raw,
                    }
                )
        page_count = doc.page_count
    chunks = chunk_blocks(blocks)
    for c in chunks:
        c["tokens"] = tokenize(c["text"])
    return {"page_count": page_count, "blocks": blocks, "chunks": chunks, "tokenizer": tokenizer_id()}


def render_page_png(path: str, page_no: int, scale: float, highlight_bboxes: list[list[float]]) -> bytes:
    """해당 페이지를 PNG로 렌더링한다. 하이라이트는 메모리 사본에만 그리고 원본 파일은 바꾸지 않는다."""
    import pymupdf

    with pymupdf.open(path) as doc:
        page = doc[page_no - 1]
        for bbox in highlight_bboxes:
            rect = pymupdf.Rect(bbox) + (-2, -2, 2, 2)
            page.draw_rect(
                rect,
                color=HIGHLIGHT_RGB,
                fill=HIGHLIGHT_RGB,
                fill_opacity=0.18,
                stroke_opacity=0.85,
                width=0.8,
                overlay=True,
            )
        longest = max(page.rect.width, page.rect.height) or 1
        eff = min(scale, MAX_RENDER_PX / longest)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(eff, eff), alpha=False)
        return pix.tobytes("png")
