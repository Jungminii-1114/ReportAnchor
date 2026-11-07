"""PyMuPDF 환경에서 업로드 PDF를 기존 추출·청킹 계약으로 변환한다."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymupdf
from pdf_extract import extract_pdf, save_result
from rag_data import prepare_dataset


def ingest(pdf: Path, output: Path, final_pdf: Path | None = None) -> dict:
    with pymupdf.open(pdf) as document:
        if not document.is_pdf or document.needs_pass:
            raise ValueError("암호가 없는 PDF 파일을 사용하세요.")
        if not 1 <= document.page_count <= 300:
            raise ValueError("PDF는 1~300페이지까지 업로드할 수 있습니다.")
        page_count = document.page_count
    result = extract_pdf(pdf)
    if final_pdf is not None:
        result = replace(result, document=replace(result.document, local_file=str(final_pdf),
                                                  local_file_dir=str(final_pdf.parent)))
    if not result.blocks:
        raise ValueError("추출할 텍스트가 없습니다. 스캔 PDF는 OCR 처리 후 추가하세요.")
    parsed = save_result(result, output / "extracted")
    report = prepare_dataset(parsed, out=output / "rag")
    if final_pdf is not None:
        final_parsed = final_pdf.parent / parsed.relative_to(output)
        summary_path = parsed / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary["output_dir"] = str(final_parsed)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        report["input_dir"] = str(final_parsed)
        report["output_dir"] = str(final_pdf.parent / "rag")
        (output / "rag/chunking_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not report["chunk_count"]:
        raise ValueError("검색할 텍스트 청크를 만들지 못했습니다.")
    return dict(chunks="rag/chunks.jsonl", blocks=str((parsed / "blocks.jsonl").relative_to(output)),
                page_count=page_count, chunk_count=report["chunk_count"],
                block_count=report["block_count"], doc_id=report["doc_id"],
                version=report["version_sha256"],
                no_native_text_pages=[page.pdf_page for page in result.pages if not page.char_count])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--final-pdf", type=Path, help="추출 후 저장될 원본 PDF의 최종 로컬 경로")
    args = parser.parse_args()
    try:
        result = ingest(args.pdf, args.out, args.final_pdf)
    except ValueError as error:
        print(json.dumps(dict(status="error", error=str(error)), ensure_ascii=False))
        return 2
    except Exception:
        print(json.dumps(dict(status="error", error="PDF를 해석하지 못했습니다. 파일 형식을 확인하세요."), ensure_ascii=False))
        return 2
    print(json.dumps(dict(status="success", data=result), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
