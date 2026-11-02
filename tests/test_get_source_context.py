"""N-02의 원문 경계·확장·병합·예산·오류·CLI를 검증한다."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from rag.contracts import ContextExpansionConfig as Config,DocumentRef,RetrievalCandidate,RetrievalCandidates
from rag.nodes.get_source_context import build_chunk_lookup,get_source_context,group_blocks_by_page,load_retrieval_result


def block(key,text,page=1):
    return dict(block_id=key,text=text,pdf_page=page,doc_id='doc',version_sha256='v1',
                source_locator=dict(type='pdf_page_bbox',pdf_page=page,bbox=[0,0,100,10],units='pt',coordinate_space='pymupdf_unrotated_page'),
                verification_status='unverified',extraction_method='native_text')
def chunk(key,blocks,ranges):
    spans=[dict(block_id=k,pdf_page=blocks[k]['pdf_page'],start_char=a,end_char=b,source_locator=deepcopy(blocks[k]['source_locator'])) for k,a,b in ranges]
    text='\n'.join(blocks[k]['text'][a:b] for k,a,b in ranges)
    return dict(chunk_id=key,doc_id='doc',version_sha256='v1',text=text,search_text=text,source_spans=spans,
                source_block_ids=list(dict.fromkeys(s['block_id'] for s in spans)),pdf_pages=sorted({s['pdf_page'] for s in spans}))
def cand(key='c1',rank=1,doc='doc',version='v1'):
    return RetrievalCandidate(key,doc,version,rank,{'bm25_score':1.0})


class SourceContextTests(unittest.TestCase):
    def setUp(self):
        self.blocks={b['block_id']:b for b in [block('b900','ABCDE'),block('b002','FGHIJ'),block('b001','KLMNO'),block('p2','PQRST',2)]}
        self.c1=chunk('c1',self.blocks,[('b002',1,4)])
        self.lookup=build_chunk_lookup([self.c1]);self.scope=[DocumentRef('doc','v1')]
    def run_node(self,candidates=None,config=None,lookup=None,blocks=None,scope=None):
        data=RetrievalCandidates('request',candidates if candidates is not None else [cand()],{},'dev')
        return get_source_context(data,self.lookup if lookup is None else lookup,self.blocks if blocks is None else blocks,
                                  document_scope=self.scope if scope is None else scope,config=config)
    def check_roundtrip(self,bundle):
        self.assertEqual(bundle.char_count,sum(len(i.text) for i in bundle.items))
        self.assertLessEqual(bundle.char_count,bundle.config.max_total_chars)
        for i in bundle.items:
            self.assertEqual(i.text,'\n'.join(s.text for s in i.source_spans))
            for s in i.source_spans:self.assertEqual(s.text,self.blocks[s.block_id]['text'][s.start_char:s.end_char])
    def test_fixed_length_preserves_raw_offsets(self):
        r=self.run_node(config=Config(3,3,100));self.assertEqual(r.status,'success')
        self.assertEqual(r.data.items[0].text,'E\nFGHIJ\nK');self.assertEqual(r.data.items[0].anchors[0].text,'GHI')
        self.check_roundtrip(r.data)
    def test_zero_expansion(self):
        r=self.run_node(config=Config(0,0,100));self.assertEqual(r.data.items[0].text,'GHI');self.assertFalse(r.data.budget_limited)
    def test_page_boundary(self):
        r=self.run_node();self.assertEqual(r.data.items[0].text,'ABCDE\nFGHIJ\nKLMNO');self.assertEqual(r.data.items[0].pdf_pages,[1])
        self.assertEqual(r.data.items[0].verification_status,'unverified')
    def test_full_page_anchor(self):
        c=chunk('full',self.blocks,[('b900',0,5),('b002',0,5),('b001',0,5)])
        r=self.run_node([cand('full')],lookup=build_chunk_lookup([c]));self.assertEqual(r.data.items[0].text,c['text']);self.check_roundtrip(r.data)
    def test_overlapping_windows_merge_without_losing_candidates(self):
        c=chunk('c2',self.blocks,[('b001',0,2)])
        r=self.run_node([cand(),cand('c2',2)],Config(3,3,100),build_chunk_lookup([self.c1,c]))
        self.assertEqual(len(r.data.items),1);self.assertEqual(r.data.items[0].text,'E\nFGHIJ\nKLMNO')
        self.assertEqual([a.chunk_id for a in r.data.items[0].anchors],['c1','c2']);self.assertEqual(r.data.resolved_candidates,2);self.check_roundtrip(r.data)
    def test_duplicate_keeps_best_rank(self):
        r=self.run_node([cand(rank=3),cand(rank=1)]);self.assertEqual(r.data.resolved_candidates,1)
        self.assertEqual(r.data.items[0].anchors[0].rank,1);self.assertEqual(r.diagnostics['deduplicate_candidates'].count,1)
    def test_conflicting_duplicate_identity(self):
        r=self.run_node([cand(),cand(version='v2')],scope=[DocumentRef('doc','v1'),DocumentRef('doc','v2')]);self.assertEqual(r.status,'error');self.assertIsNone(r.data)
    def test_all_candidates_scope_checked_before_processing(self):
        r=self.run_node([cand(),cand('outside',2,doc='other')]);self.assertEqual(r.status,'error');self.assertIsNone(r.data)
        self.assertEqual(r.diagnostics['get_source_context'].error_code,'DOCUMENT_SCOPE_MISMATCH')
    def test_empty_scope_is_invalid(self):
        r=self.run_node(scope=[]);self.assertEqual(r.status,'error');self.assertIsNone(r.data)
    def test_candidate_chunk_version_mismatch(self):
        r=self.run_node([cand(version='v2')],scope=[DocumentRef('doc','v2')]);self.assertEqual(r.status,'error');self.assertIsNone(r.data)
        self.assertEqual(r.diagnostics['get_source_context'].error_code,'DOCUMENT_VERSION_MISMATCH')
    def test_block_identity_mismatch(self):
        self.blocks['b002']['version_sha256']='v2';r=self.run_node();self.assertEqual(r.status,'error');self.assertIsNone(r.data)
    def test_missing_one_chunk_returns_partial(self):
        r=self.run_node([cand(),cand('missing',2)]);self.assertEqual(r.status,'partial');self.assertEqual(r.data.resolved_candidates,1)
        self.assertEqual(r.data.skipped_chunk_ids,['missing']);self.assertEqual(r.diagnostics['candidate_2'].error_code,'CHUNK_NOT_FOUND')
    def test_missing_all_sources_is_error(self):
        r=self.run_node([cand('missing')]);self.assertEqual(r.status,'error');self.assertIsNone(r.data)
    def test_missing_block_preserves_unaffected_candidate(self):
        c=chunk('other',self.blocks,[('b900',0,3)]);del self.blocks['b002']
        r=self.run_node([cand('other'),cand('c1',2)],Config(0,0,100),build_chunk_lookup([c,self.c1]))
        self.assertEqual(r.status,'partial');self.assertEqual(r.data.resolved_candidates,1);self.assertEqual(r.diagnostics['candidate_2'].error_code,'BLOCK_NOT_FOUND')
    def test_corrupt_text_fails_without_raw_text_in_diagnostics(self):
        self.c1['text']='sensitive corrupt body';r=self.run_node();self.assertEqual(r.status,'error');self.assertIsNone(r.data)
        self.assertNotIn('sensitive',json.dumps(asdict(r)))
    def test_invalid_character_ranges(self):
        for a,b in [(-1,4),(1,99),(True,3),(3,3)]:
            c=deepcopy(self.c1);c['source_spans'][0].update(start_char=a,end_char=b)
            with self.subTest(a=a,b=b):self.assertEqual(self.run_node(lookup=build_chunk_lookup([c])).status,'error')
    def test_page_and_locator_disagreement(self):
        for field in ['page','bbox']:
            c=deepcopy(self.c1)
            if field=='page':c['pdf_pages']=[2]
            else:c['source_spans'][0]['source_locator']['bbox']=[1,1,10,10]
            with self.subTest(field=field):self.assertEqual(self.run_node(lookup=build_chunk_lookup([c])).status,'error')
    def test_noncontiguous_spans_rejected(self):
        c=chunk('gap',self.blocks,[('b900',0,5),('b001',0,2)])
        r=self.run_node([cand('gap')],lookup=build_chunk_lookup([c]));self.assertEqual(r.status,'error')
        self.assertEqual(r.diagnostics['get_source_context'].error_code,'NONCONTIGUOUS_SOURCE_SPANS')
    def test_file_order_not_identifier_order(self):
        self.assertEqual([b['block_id'] for b in group_blocks_by_page(self.blocks)[('doc','v1',1)]],['b900','b002','b001'])
    def test_output_does_not_modify_or_alias_inputs(self):
        before=deepcopy(self.blocks);r=self.run_node();r.data.items[0].source_spans[0].source_locator['bbox'][0]=77;self.assertEqual(self.blocks,before)
    def test_optional_expansion_can_fall_back_to_complete_anchor(self):
        r=self.run_node(config=Config(200,200,3));self.assertEqual(r.status,'success');self.assertEqual(r.data.items[0].text,'GHI')
        self.assertTrue(r.data.budget_limited);self.assertEqual(r.diagnostics['context_expansion_1'].status,'skipped');self.check_roundtrip(r.data)
    def test_budget_never_truncates_anchor(self):
        r=self.run_node(config=Config(0,0,2));self.assertEqual(r.status,'error');self.assertIsNone(r.data)
        self.assertEqual(r.diagnostics['candidate_1'].error_code,'CONTEXT_BUDGET_EXCEEDED')
    def test_budget_preserves_higher_rank(self):
        c=chunk('c2',self.blocks,[('p2',0,4)])
        r=self.run_node([cand('c2',2),cand()],Config(0,0,3),build_chunk_lookup([self.c1,c]));self.assertEqual(r.status,'partial')
        self.assertEqual(r.data.items[0].anchors[0].chunk_id,'c1');self.check_roundtrip(r.data)
    def test_empty_candidates_outcome(self):
        r=self.run_node([]);self.assertEqual(r.status,'success');self.assertEqual(r.outcome,'no_candidates');self.assertEqual(r.data.items,[])
    def test_multi_page_anchor_without_cross_page_expansion(self):
        c=chunk('multi',self.blocks,[('b001',2,5),('p2',0,2)])
        r=self.run_node([cand('multi')],lookup=build_chunk_lookup([c]));self.assertEqual([i.pdf_pages for i in r.data.items],[[1],[2]])
        self.assertEqual([i.anchors[0].text for i in r.data.items],['MNO','PQ']);self.check_roundtrip(r.data)
    def test_mixed_verification_status_remains_mixed(self):
        self.blocks['b900']['verification_status']='verified';self.assertEqual(self.run_node().data.items[0].verification_status,'mixed')
    def test_evidence_id_is_deterministic(self):
        self.assertEqual(self.run_node().data.items[0].evidence_id,self.run_node().data.items[0].evidence_id)
    def test_nonfinite_bbox_rejected(self):
        self.blocks['b002']['source_locator']['bbox'][0]=float('nan');self.assertEqual(self.run_node().status,'error')
    def test_config_rejects_bool_negative_and_zero_total(self):
        for kwargs in [dict(before_chars=True),dict(after_chars=-1),dict(max_total_chars=0)]:
            with self.subTest(kwargs=kwargs),self.assertRaises((TypeError,ValueError)):Config(**kwargs)
    def test_cli_json_and_explicit_scope(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);cp=d/'chunks.jsonl';bp=d/'blocks.jsonl';cp.write_text(json.dumps(self.c1)+'\n');bp.write_text('\n'.join(json.dumps(b) for b in self.blocks.values()))
            cmd=[sys.executable,'-m','rag.nodes.get_source_context','--chunks',str(cp),'--blocks',str(bp),'--chunk-id','c1','--before-chars','3','--after-chars','3']
            p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
            data=json.loads(p.stdout);self.assertEqual(set(data),{'status','data','diagnostics','outcome'});self.assertEqual(data['data']['items'][0]['text'],'E\nFGHIJ\nK')
            p=subprocess.run(cmd+['--scope','other','v1'],cwd=ROOT,capture_output=True,text=True);self.assertEqual(p.returncode,2);self.assertEqual(json.loads(p.stdout)['status'],'error')
    def test_partial_upstream_allowed_error_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r.json';data=asdict(RetrievalCandidates('request',[cand()],{},'dev'))
            p.write_text(json.dumps({'status':'partial','data':data}));self.assertEqual(load_retrieval_result(p).candidates[0].chunk_id,'c1')
            p.write_text(json.dumps({'status':'error','data':None}))
            with self.assertRaises(ValueError):load_retrieval_result(p)
    def test_context_content_change_changes_evidence_id(self):
        before=self.run_node().data.items[0].evidence_id
        self.blocks['b900']['text']='ABCDX'
        self.assertNotEqual(before,self.run_node().data.items[0].evidence_id)
    def test_unicode_budget_counts_characters_and_keeps_raw_text(self):
        self.blocks['b002']['text']='가나다라마'
        c=chunk('unicode',self.blocks,[('b002',1,4)])
        r=self.run_node([cand('unicode')],Config(200,200,3),build_chunk_lookup([c]))
        self.assertEqual(r.data.items[0].text,'나다라');self.assertEqual(r.data.char_count,3)
    def test_malformed_block_row_returns_structured_cli_error(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);cp=d/'c.jsonl';bp=d/'b.jsonl';cp.write_text(json.dumps(self.c1));bp.write_text('[]\n')
            cmd=[sys.executable,'-m','rag.nodes.get_source_context','--chunks',str(cp),'--blocks',str(bp),'--chunk-id','c1']
            p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(p.returncode,2);self.assertEqual(json.loads(p.stdout)['status'],'error')

if __name__=='__main__':unittest.main()
