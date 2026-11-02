"""Prepared v4 regions -> local Ollama. Standard library only; no model calls by default."""
import argparse,base64,hashlib,json,time,urllib.request,urllib.error,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OPTIONS={'temperature':0,'seed':42,'num_ctx':8192}
def read_json(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def save(p,data):
 p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_suffix(p.suffix+'.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(p)
def validate(s,kind):
 s=s.strip()
 if s.startswith('```'):s=s.split('\n',1)[1].rsplit('```',1)[0]
 d=json.loads(s)
 if not isinstance(d,dict):raise ValueError('Output must be an object')
 if 'issues' in d and (not isinstance(d['issues'],list) or not all(isinstance(x,str) for x in d['issues'])):raise ValueError('issues must be a string array')
 if kind=='table':
  if not isinstance(d.get('cells'),list):raise ValueError('cells must be an array')
  if not isinstance(d.get('table_title'),str):raise ValueError('table_title must be a string')
  if not isinstance(d.get('notes'),list) or not all(isinstance(x,str) for x in d['notes']):raise ValueError('notes must be a string array')
  for x in d['cells']:
   if not isinstance(x,dict):raise ValueError('Each cell must be an object')
   for k in ['row_path','column_path']:
    if not isinstance(x.get(k),list) or not x[k] or not all(isinstance(v,str) for v in x[k]):raise ValueError(k+' must be a nonempty string array')
   for k in ['value_raw','unit']:
    if not isinstance(x.get(k),str):raise ValueError(k+' must be a string')
 else:
  if not isinstance(d.get('items'),list):raise ValueError('items must be an array')
  for x in d['items']:
   if not isinstance(x,dict) or not all(isinstance(x.get(k),str) for k in ['group','item','value','unit']):raise ValueError('Invalid metric item')
 return d

def request(url,payload=None,timeout=30):
 req=urllib.request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode('utf-8') if payload is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=timeout) as response:return json.loads(response.read().decode('utf-8'))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--run',action='store_true');ap.add_argument('--model',default='qwen3-vl:4b-instruct-q4_K_M');ap.add_argument('--only',action='append',help='Task ID; repeat to select multiple tasks');ap.add_argument('--output',default='results');ap.add_argument('--timeout',type=float,default=900);a=ap.parse_args();tasks=read_json(ROOT/'tasks.json');known={x['task_id'] for x in tasks}
 if a.only and set(a.only)-known:ap.error('Unknown task IDs: '+str(set(a.only)-known))
 tasks=[x for x in tasks if not a.only or x['task_id'] in a.only];slug=a.model.replace(':','_').replace('/','_');out=ROOT/a.output/slug;out.mkdir(parents=True,exist_ok=True);rows=[]
 for t in tasks:
  if hashlib.sha256((ROOT/t['image']).read_bytes()).hexdigest()!=t['image_sha256']:raise ValueError('Input image changed: '+t['task_id'])
 print('Model:',a.model,'| tasks:',len(tasks),'| mode:','RUN' if a.run else 'PREPARE ONLY',flush=True)
 if a.run:
  try:installed={m.get('name') for m in request('http://127.0.0.1:11434/api/tags').get('models',[])}
  except Exception as exc:raise SystemExit('Ollama connection failed. Start Ollama, then run again. '+str(exc))
  if a.model not in installed:raise SystemExit('Model not installed: '+a.model+'; check ollama list. No downloads performed.')
 for idx,t in enumerate(tasks,1):
  tid=t['task_id'];prompt=(ROOT/t['prompt_file']).read_text(encoding='utf-8');effective=prompt+t['parser_text'] if t['kind']=='table' else prompt;options={**OPTIONS,'num_predict':4096 if t['kind']=='table' else 2048};signature=hashlib.sha256(json.dumps({'model':a.model,'prompt':effective,'image_sha256':t['image_sha256'],'options':options},sort_keys=True,ensure_ascii=False).encode()).hexdigest();target=out/(tid+'.json')
  if a.run and target.exists():
   existing=read_json(target)
   if existing.get('input_signature')==signature and existing.get('status') in ['schema_valid_content_unverified','empty_extraction']:
    print(f'[{idx}/{len(tasks)}] {tid}: reused saved result',flush=True);rows.append(existing);continue
  md={**t['metadata'],'model':a.model,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'prompt_file':t['prompt_file'],'extraction_method':'vlm','options':options};row={'task_id':tid,'kind':t['kind'],'purpose':t['purpose'],'input_signature':signature,'metadata':md,'baseline_file':t['baseline'],'image_file':t['image'],'status':'prepared_no_call'}
  save(out/(tid+'_input.json'),{'metadata':md,'effective_prompt':effective,'image_file':t['image'],'input_signature':signature})
  print(f'[{idx}/{len(tasks)}] {tid}: '+('running' if a.run else 'prepared'),flush=True)
  if a.run:
   started=time.perf_counter()
   try:
    payload={'model':a.model,'prompt':effective,'images':[base64.b64encode((ROOT/t['image']).read_bytes()).decode('ascii')],'stream':False,'format':'json','options':options};raw=request('http://127.0.0.1:11434/api/generate',payload,a.timeout);save(out/(tid+'_raw.json'),raw);extraction=validate(raw['response'],t['kind']);row['extraction']=extraction;row['status']='schema_valid_content_unverified' if extraction['cells' if t['kind']=='table' else 'items'] else 'empty_extraction';row['ollama_metrics']={k:raw.get(k) for k in ['total_duration','load_duration','prompt_eval_count','prompt_eval_duration','eval_count','eval_duration','done_reason']}
   except Exception as exc:row.update(status='failed',error=str(exc))
   row['wall_seconds']=time.perf_counter()-started
   print('  '+row['status']+f" ({row['wall_seconds']:.1f}s)"+(' '+row.get('error','') if row['status']=='failed' else ''),flush=True)
  save(target,row);rows.append(row);save(out/'summary.json',{'model':a.model,'tasks':rows,'note':'Schema validity is not content accuracy. No F1 computed without gold.'})
 save(out/'summary.json',{'model':a.model,'tasks':rows,'note':'Schema validity is not content accuracy. No F1 computed without gold.'});print('Saved:',out,flush=True)
if __name__=='__main__':main()
