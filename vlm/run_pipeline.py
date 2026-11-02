"""Any local PDF -> native parser -> Docling -> v4 selection -> crops -> Ollama.
Default: prepare crops/tasks only. --run explicitly enables local VLM calls.
"""
import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

def module(name,path):
    spec = importlib.util.spec_from_file_location(name,path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

def command(script,*args):
    subprocess.run([sys.executable,str(script),*map(str,args)],check=True)

def check_docling(folder,sha,page_count):
    settings=read(folder/'settings.json')
    if settings.get('sha256')!=sha:
        raise ValueError('Docling cache PDF hash does not match input PDF')
    for pn in range(1,page_count+1):
        raw=read(folder/f'p{pn:03d}.json')
        if str(pn) not in raw.get('pages',{}):
            raise ValueError(f'Docling page {pn} has missing/wrong page provenance')
    summary=folder/'run_summary.json'
    if summary.exists():
        rows=read(summary)
        latest={int(row['pdf_page']):row for row in rows}
        bad=[pn for pn in range(1,page_count+1) if pn in latest and
             latest[pn].get('status','').lower() not in
             ['success','conversionstatus.success','reused_valid_json']]
        if bad:
            raise ValueError(f'Docling conversion was not successful on pages: {bad}; see {summary}')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pdf',required=True,type=Path)
    ap.add_argument('--output',type=Path,default=ROOT/'outputs')
    ap.add_argument('--run',action='store_true',help='Call local Ollama; otherwise prepare only')
    ap.add_argument('--include-review',action='store_true',help='Include orange review regions as well as red candidates')
    ap.add_argument('--model',default='qwen3-vl:4b-instruct-q4_K_M')
    ap.add_argument('--timeout',type=float,default=900)
    ap.add_argument('--ollama-url',default='http://127.0.0.1:11434')
    ap.add_argument('--table-timeout',type=float,default=15)
    ap.add_argument('--docling-python',default=sys.executable,help='Python executable of a separate Docling environment')
    ap.add_argument('--docling-cache',type=Path,help='Optional existing per-page Docling JSON folder; PDF hash must match')
    a=ap.parse_args()
    import pymupdf
    pdf=a.pdf.resolve(strict=True)
    sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
    with pymupdf.open(pdf) as doc:
        if doc.needs_pass:
            ap.error('Encrypted PDF requires an unlocked local copy')
        count=len(doc)
        if not count:
            ap.error('PDF has no pages')
        # Existing routing coordinates are defined for unrotated PDF pages.
        if any(p.rotation for p in doc):
            ap.error('Rotated PDF pages are not supported by these rules yet; normalize page rotation first')
    key='doc_'+sha[:16]
    work=a.output.resolve()/key
    folder=work/'results'/key
    config=work/'config.json'
    write(config,{'output':'results','documents':[{'id':key,'pdf':str(pdf),'review_pages':[]}]})
    print(f'PDF: {pdf.name} | pages: {count} | document: {key}',flush=True)
    print('[1/5] Native parsing',flush=True)
    command(ROOT/'pipeline/01_parser_python.py','--config',config,'--table-timeout',a.table_timeout)
    native=read(folder/'parser_python.json')
    print('[2/5] Docling OCR/layout/table/classification',flush=True)
    if a.docling_cache:
        rawfolder=a.docling_cache.resolve()
    else:
        subprocess.run([a.docling_python,str(ROOT/'pipeline/02_docling.py'),
                        '--config',str(config),'--all-pages','--resume'],check=True)
        rawfolder=folder/'docling'
    check_docling(rawfolder,sha,count)
    print('[3/5] Apply combined routing and frozen v4 refinement',flush=True)
    combined=module('combined_rules',ROOT/'pipeline/03_docling_postprocess.py')
    v4=module('v4_rules',ROOT/'selection_v4/refine_and_freeze.py')
    records=[]
    with pymupdf.open(pdf) as doc:
        for pn,page in enumerate(doc,1):
            raw=read(rawfolder/f'p{pn:03d}.json')
            previous=combined.process(raw,pn,page.rect.width,page.rect.height)
            records.append(v4.refine(raw,native['pages'][pn-1],previous,page))
    selectiondir=work/'selection_v4'
    write(selectiondir/(key+'.json'),records)
    codehashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                [ROOT/'pipeline/01_parser_python.py',ROOT/'pipeline/02_docling.py',
                 ROOT/'pipeline/03_docling_postprocess.py',ROOT/'selection_v4/refine_and_freeze.py']}
    write(selectiondir/'provenance.json',{'rule_version':v4.VERSION,'pdf_sha256':sha,'code_sha256':codehashes})
    print('[4/5] Selected regions -> PNG images -> VLM tasks',flush=True)
    args=['--pdf',pdf,'--documents',key,'--selection-dir',selectiondir,
          '--output',work/'vlm','--model',a.model,'--timeout',a.timeout,'--ollama-url',a.ollama_url]
    if a.include_review:
        args.append('--include-review')
    if a.run:
        args.append('--run')
    command(ROOT/'run/run_selected.py',*args)
    print('[5/5] Save document-level output with source locations',flush=True)
    tasksummary=work/'vlm/results'/a.model.replace(':','_').replace('/','_')/'summary.json'
    taskrows=read(tasksummary)['tasks'] if tasksummary.exists() else []
    rows_by_id={r['task_id']:r for r in taskrows}
    tasks=read(work/'vlm/tasks.json')
    pages=[]
    for entry,n in zip(records,native['pages']):
        pn=entry['pdf_page']
        pages.append({'pdf_page':pn,'native':n,'docling':read(rawfolder/f'p{pn:03d}.json'),
                      'selection':entry,'vlm_results':[rows_by_id[t['task_id']] for t in tasks
                          if t['pdf_page']==pn and t['task_id'] in rows_by_id]})
    failed=[r['task_id'] for r in taskrows if r['status']=='failed']
    result={'document':{'doc_id':key,'file_name':pdf.name,'version_sha256':sha,'page_count':count},
            'rule_version':v4.VERSION,'mode':'run' if a.run else 'prepare_only',
            'status':'completed_with_vlm_failures' if failed else ('completed' if a.run else 'prepared_no_vlm_calls'),
            'vlm_model':a.model,'include_review':a.include_review,'failed_tasks':failed,
            'quality_status':'content_unverified','pages':pages,
            'note':'Native/Docling/VLM outputs are preserved separately; no automatic claim of corrected content or RAG indexing.'}
    write(work/'document_result.json',result)
    print('Saved:',work/'document_result.json',flush=True)
    if failed:
        raise SystemExit(f'{len(failed)} VLM calls failed; results saved, rerun to retry matching tasks')

if __name__=='__main__':
    main()
