"""BM25 후보·원문 대응과 빈 결과 처리를 검증한다."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('REPORT_AGENT_ROOT', str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(ROOT))
candidate = os.environ.get('BM25_MODULE_PATH')
if candidate:
    spec = importlib.util.spec_from_file_location('rag.retrieval.bm25', candidate)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
else:
    from rag.retrieval import bm25 as module


def chunk(identifier, text, page=1):
    return {'chunk_id': identifier, 'search_text': text, 'text': text,
            'pdf_pages': [page], 'source_block_ids': [f'block-{identifier}']}


class BM25Tests(unittest.TestCase):
    def setUp(self):
        self.tokenizer = patch.object(module, 'tokenize', lambda text: text.lower().split())
        self.tokenizer.start()
        self.addCleanup(self.tokenizer.stop)

    def test_jsonl_blank_lines_and_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chunks.jsonl'
            row = chunk('a', '한국어 예산')
            path.write_text('\n' + json.dumps(row, ensure_ascii=False) + '\n\n', encoding='utf-8')
            self.assertEqual(module.load_chunks(path), [row])
            path.write_text('{}\n{broken}\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'chunk_id'):
                module.load_chunks(path)
            path.write_text(json.dumps(row) + '\n{broken}\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'chunks.jsonl:2'):
                module.load_chunks(path)

    def test_empty_chunk_does_not_shift_provenance(self):
        rows = [chunk('blank', ''), chunk('cat', 'cat food', 9),
                chunk('dog', 'dog walk', 3), chunk('bird', 'bird wings', 4)]
        original = json.dumps(rows)
        retriever = module.BM25Retriever(rows)
        self.assertEqual(retriever.skipped_chunk_ids, ['blank'])
        hit = retriever.search('cat')[0]
        self.assertEqual(hit['chunk_id'], 'cat')
        self.assertEqual(hit['chunk']['pdf_pages'], [9])
        self.assertEqual(hit['chunk']['source_block_ids'], ['block-cat'])
        hit['chunk']['pdf_pages'][0] = 999
        self.assertEqual(retriever.search('cat')[0]['chunk']['pdf_pages'], [9])
        self.assertEqual(json.dumps(rows), original)

    def test_zero_score_match_retained_and_unmatched_docs_excluded(self):
        # 두 문서 중 한 문서에만 나오는 단어의 IDF는 이 구현에서 0이다.
        retriever = module.BM25Retriever([chunk('a', 'cat'), chunk('b', 'dog')])
        hits = retriever.search('cat')
        self.assertEqual([h['chunk_id'] for h in hits], ['a'])
        self.assertEqual(hits[0]['score'], 0.0)
        self.assertEqual(retriever.search('unknown'), [])
        self.assertEqual(retriever.search('   '), [])

    def test_repeat_search_reuses_index_and_stable_ties(self):
        retriever = module.BM25Retriever([chunk('a', 'cat'), chunk('b', 'cat'),
                                         chunk('c', 'dog'), chunk('d', 'bird')])
        index = retriever.index
        self.assertEqual([h['chunk_id'] for h in retriever.search('cat', 1)], ['a'])
        self.assertEqual([h['rank'] for h in retriever.search('cat')], [1, 2])
        self.assertIs(retriever.index, index)

    def test_bad_inputs_are_explicit(self):
        for rows in [[], [chunk('a', '')], [chunk('a', 'cat'), chunk('a', 'dog')],
                     [{'chunk_id': 'a', 'search_text': None}]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                module.BM25Retriever(rows)
        retriever = module.BM25Retriever([chunk('a', 'cat')])
        for top_k in [0, -1, True, 1.5]:
            with self.subTest(top_k=top_k), self.assertRaises(ValueError):
                retriever.search('cat', top_k)


if __name__ == '__main__':
    unittest.main()
