"""Frozen v4 JSON -> PDF crops -> task manifest -> existing local Ollama runner.
No VLM call unless --run is supplied. PDF bytes must match the selection provenance.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pymupdf

HERE = Path(__file__).resolve().parent
VLM = HERE.parent

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def kind_for(region):
    if any(x.startswith('#/tables/') for x in region.get('source_refs', [])):
        return 'table'
    if any('numeric_context' in x for x in region.get('reason', [])):
        return 'metric'
    return 'diagram'

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pdf', type=Path, help='Single PDF override; use with one --documents value')
    ap.add_argument('--pdf-dir', default='.', help='KISTEP.pdf, ETRI.pdf, AX.pdf directory')
    ap.add_argument('--selection-dir', type=Path, default=VLM/'selection_v4/results')
    ap.add_argument('--documents', nargs='+', default=['KISTEP','ETRI','AX'])
    ap.add_argument('--include-review', action='store_true', help='Also process orange review_required regions')
    ap.add_argument('--refresh-selection', action='store_true', help='Reapply existing v4 to cached pipeline results first')
    ap.add_argument('--output', type=Path, default=HERE/'selected_v4')
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--model', default='qwen3-vl:4b-instruct-q4_K_M')
    ap.add_argument('--timeout', type=float, default=900)
    ap.add_argument('--ollama-url', default='http://127.0.0.1:11434')
    a = ap.parse_args()
    pdfdir = Path(a.pdf_dir).resolve()
    out = a.output.resolve()
    if a.pdf and (len(a.documents)!=1 or a.refresh_selection):
        ap.error('--pdf requires one document and cannot use --refresh-selection')
    if a.refresh_selection:
        subprocess.run([sys.executable, str(VLM/'selection_v4/refine_and_freeze.py'),
                        '--root', str(VLM/'experiments/selection_full'), '--pdf-dir', str(pdfdir),
                        '--output', str(a.selection_dir.resolve())], check=True)
    decisions = {'vlm_candidate'} | ({'review_required'} if a.include_review else set())
    # Preflight every source before writing crops or calling a model.
    sources = []
    for name in a.documents:
        pdf = a.pdf.resolve() if a.pdf else pdfdir/(name+'.pdf')
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        pages = read(a.selection_dir/(name+'.json'))
        with pymupdf.open(pdf) as doc:
            for record in pages:
                pn = record['pdf_page']
                if not 1 <= pn <= len(doc):
                    raise ValueError(f'{name}: invalid page {pn}')
                for r in record['regions']:
                    if r.get('version_sha256') != digest:
                        raise ValueError(f'{name}: PDF hash differs from v4 input; use original PDF')
                    b = r['bbox']
                    if len(b)!=4 or not all(isinstance(v,(int,float)) for v in b) or b[0]>=b[2] or b[1]>=b[3]:
                        raise ValueError(f'{name}: invalid bbox {b}')
        sources.append((name,pdf,digest,pages))
    tasks = []
    for name,pdf,digest,pages in sources:
        with pymupdf.open(pdf) as doc:
            for record in pages:
                page = doc[record['pdf_page']-1]
                for r in record['regions']:
                    if r['decision'] not in decisions:
                        continue
                    tid = name+'_'+r['region_id']
                    b = pymupdf.Rect(r['bbox'])
                    # Small context padding; record exact input separately from selected bbox.
                    clip = pymupdf.Rect(b.x0-6,b.y0-6,b.x1+6,b.y1+6) & page.rect
                    if clip.is_empty:
                        raise ValueError(f'{tid}: crop outside page')
                    image = out/'inputs'/(tid+'.png')
                    image.parent.mkdir(parents=True, exist_ok=True)
                    page.get_pixmap(matrix=pymupdf.Matrix(2,2),clip=clip,alpha=False).save(image)
                    text = page.get_text('text',clip=clip)
                    baseline = out/'inputs'/(tid+'_baseline.json')
                    write(baseline, {'doc_id':r['doc_id'],'pdf_page':record['pdf_page'],
                                     'bbox':list(clip),'native_text':text})
                    kind = kind_for(r)
                    prompt = {'table':'table_fewshot.txt','metric':'metric_pilot.txt','diagram':'diagram.txt'}[kind]
                    tasks.append({'task_id':tid,'document':name,'pdf_page':record['pdf_page'],
                        'kind':kind,'purpose':'automatic_v4_selection','image':str(image),
                        'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
                        'baseline':str(baseline),'parser_text':text,
                        'prompt_file':str(HERE/'prompts'/prompt),
                        'metadata':{'doc_id':r['doc_id'],'version_sha256':digest,
                            'region_id':r['region_id'],'pdf_page':record['pdf_page'],
                            'selection_bbox':r['bbox'],'selection_decision':r['decision'],
                            'selection_reasons':r.get('reason',[]),'source_refs':r.get('source_refs',[]),
                            'selection_rule_version':r['rule_version'],'verification_status':'unverified',
                            'source_locator':{'type':'pdf_page_bbox','pdf_page':record['pdf_page'],
                                'bbox':list(clip),'units':'pt','coordinate_space':'pymupdf_unrotated_page'}}})
    manifest = out/'tasks.json'
    write(manifest,tasks)
    write(out/'selection_summary.json',{'tasks':len(tasks),'decisions':sorted(decisions),
        'by_document':{n:sum(t['document']==n for t in tasks) for n in a.documents},
        'by_kind':{k:sum(t['kind']==k for t in tasks) for k in ['table','metric','diagram']},
        'note':'Kinds select an output schema, not a verified semantic classification. Crop context/quality remain unverified.'})
    print(f'Prepared {len(tasks)} selected regions: {manifest}',flush=True)
    if not tasks:
        print('No selected regions; no model calls.'); return
    cmd = [sys.executable,str(HERE/'run_vlm.py'),'--tasks',str(manifest),
           '--output',str(out/'results'),'--model',a.model,'--timeout',str(a.timeout),'--ollama-url',a.ollama_url]
    if a.run:
        cmd.append('--run')
    subprocess.run(cmd,check=True)

if __name__ == '__main__':
    main()
