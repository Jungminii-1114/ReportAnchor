"""Baseline-oriented development routing; preserve v2 inputs; never call a VLM."""
import argparse,json,re,hashlib,unicodedata
from pathlib import Path
import pymupdf
VERSION='baseline-oriented-selection/4'
def norm(s):return re.sub(r'[^\w%]','',unicodedata.normalize('NFKC',s)).lower()
def area(b):return max(0,b[2]-b[0])*max(0,b[3]-b[1])
def inter(a,b):return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
def bb(p,h):
 b=p['bbox'];return [b['l'],h-b['t'],b['r'],h-b['b']] if b.get('coord_origin')=='BOTTOMLEFT' else [b['l'],b['t'],b['r'],b['b']]
def refine(raw,native,old,page):
 out=json.loads(json.dumps(old));changes=[];w,h=page.rect.width,page.rect.height;pn=page.number+1;words=page.get_text('words');texts=raw.get('texts',[]);refs={t['self_ref']:t for t in texts}
 def change(r,decision,reason,evidence):
  prior=r['decision'];r['decision']=decision;r['reason']=list(dict.fromkeys(r.get('reason',[])+[reason]));r['baseline_evidence']=evidence;changes.append({'region_id':r.get('region_id'),'previous':prior,'decision':decision,'reason':reason,'evidence':evidence})
 # Gate table proposals using baseline evidence, not Docling's successful-looking structure.
 for r in out:
  candidates=[t for t in native['tables'] if inter(t['bbox'],r['bbox'])/max(1,area(t['bbox']))>=.9 and inter(t['bbox'],r['bbox'])/max(1,area(r['bbox']))>=.8]
  for t in candidates:
   matrix=t['rows'];simple=bool(matrix and len(matrix)>=2 and all(len(row)==t['column_count'] and all(c is not None for c in row) for row in matrix) and all(str(x).strip() for x in matrix[0]))
   header_hits=sum(bool(re.search(r'구\s*분|항\s*목|금\s*액|예산|비율|인원|20\d{2}년|[’\']?2[45]년|증\s*감',str(x))) for x in matrix[0]) if matrix else 0
   if not simple or header_hits<2:continue
   flat=norm(' '.join(str(x) for row in matrix for x in row));b=t['bbox'];tokens=[x[4] for x in words if b[0]<=((x[0]+x[2])/2)<=b[2] and b[1]<=((x[1]+x[3])/2)<=b[3] and len(norm(x[4]))>=2];missing=[s for s in tokens if norm(s) not in flat]
   if tokens and not missing and r['decision'] in ['vlm_candidate','review_required'] and any(ref.startswith('#/tables/') for ref in r.get('source_refs',[])):
    change(r,'parser_only','baseline_simple_table_content_preserved',{'native_rows':t['row_count'],'native_columns':t['column_count'],'source_tokens_checked':len(tokens),'missing_tokens':0,'semantic_accuracy':'not_gold_verified'})
 # Suppress page-reference numbers only with supported contents context.
 title=' '.join(t.get('text','') for t in texts if t.get('label')=='section_header' or any(p['page_no']==pn and bb(p,h)[1]<.15*h and len(t.get('text',''))<=20 for p in t.get('prov',[])));leading=[t for t in texts if re.match(r'^\s*\d{1,3}(?:\s|$)',t.get('text','')) and t.get('label') not in ['page_header','page_footer']];institutions=sum(bool(re.search(r'연구소|연구본부|사업단|연구센터',t.get('text',''))) for t in texts)
 right=[]
 for t in texts:
  if re.fullmatch(r'\d{1,3}',t.get('text','').strip()):
   for p in t.get('prov',[]):
    if p['page_no']==pn and bb(p,h)[0]>.65*w:right.append(t)
 toc=bool(re.search(r'목\s*차|contents',title,re.I)) or (len(leading)>=6 and institutions>=3) or (bool(re.search(r'개요',title)) and len(right)>=4)
 if toc:
  for r in out:
   anchor=[refs[x].get('text','').strip() for x in r.get('source_refs',[]) if x in refs]
   if 'numeric_context_window_no_semantic_link_inferred' in r.get('reason',[]) and anchor and all(re.fullmatch(r'\d{1,3}',s) for s in anchor):change(r,'exclude_vlm','contents_page_reference_not_metric',{'heading':title,'leading_numbers':len(leading),'institution_labels':institutions,'right_column_numbers':len(right),'anchors':anchor})
 # Recover native sparse-text images even when total page text exceeds 50 chars.
 # Merge vertically adjacent raster strips with aligned horizontal edges.
 images=sorted([list(x['bbox']) for x in page.get_image_info() if x['bbox'][2]-x['bbox'][0]>=.2*w],key=lambda b:(round(b[0]),b[1]));groups=[]
 for b in images:
  g=next((g for g in reversed(groups) if abs(g[0]-b[0])<=2 and abs(g[2]-b[2])<=2 and -.5<=b[1]-g[3]<=2),None)
  if g:g[1]=min(g[1],b[1]);g[3]=max(g[3],b[3])
  else:groups.append(b.copy())
 photo=[]
 for x in raw.get('pictures',[]):
  pred=sorted(x.get('meta',{}).get('classification',{}).get('predictions',[]),key=lambda q:q.get('confidence',0),reverse=True)
  if pred and pred[0].get('class_name')=='photograph' and pred[0].get('confidence',0)>=.9:
   photo += [bb(p,h) for p in x.get('prov',[]) if p['page_no']==pn]
 for g in groups:
  if native['text_characters']<50 or area(g)<.02*w*h or g[3]-g[1]<.04*h:continue
  inside=' '.join(x[4] for x in words if g[0]<=(x[0]+x[2])/2<=g[2] and g[1]<=(x[1]+x[3])/2<=g[3])
  if len(norm(inside))>0:continue
  if any(inter(g,p)/max(1,area(g))>=.8 for p in photo):continue
  covered=max([inter(g,r['bbox'])/max(1,area(g)) for r in out if r['decision'] in ['vlm_candidate','review_required']]+[0])
  if covered>=.9:continue
  r={'bbox':[max(0,g[0]),max(0,g[1]),min(w,g[2]),min(h,g[3])],'decision':'review_required','reason':['native_sparse_text_raster_group_not_fully_routed'],'source_refs':[],'verification_status':'unverified','baseline_evidence':{'native_inside_characters':len(norm(inside)),'previous_coverage':covered},'region_id':f'p{pn:04d}_new{sum("new" in q.get("region_id","") for q in out)+1:03d}'}
  out.append(r);changes.append({'region_id':r['region_id'],'previous':None,'decision':r['decision'],'reason':r['reason'][0],'evidence':r['baseline_evidence']})
 for r in out:
  r['rule_version']=VERSION;r.update({k:native[k] for k in ['doc_id','version_sha256','pdf_page']});r['source_locator']={'type':'pdf_page_bbox','pdf_page':pn,'bbox':r['bbox'],'units':'pt','coordinate_space':'pymupdf_unrotated_page'}
 return {'pdf_page':pn,'status':'processed','rule_version':VERSION,'regions':out,'changes':changes,'text_quality_status':'native_unavailable_docling_not_gold_verified' if native['text_characters']<50 and not native['near_blank'] else 'not_gold_verified'}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--pdf-dir',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();root=Path(a.root);dest=Path(a.output);dest.mkdir(exist_ok=True,parents=True);summary=[]
 for name in ['KISTEP','ETRI','AX']:
  n=json.loads((root/f'results/{name}/parser_python.json').read_text());records=[]
  with pymupdf.open(Path(a.pdf_dir)/f'{name}.pdf') as doc:
   for p in doc:
    pn=p.number+1;raw=json.loads((root/f'results/{name}/docling/p{pn:03}.json').read_text());old=json.loads((root/f'{name}_p{pn:03}.json').read_text())['methods']['combined_frozen'];records.append(refine(raw,n['pages'][pn-1],old,p))
  (dest/f'{name}.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8');summary.append({'document':name,'pages':len(records),'changed_pages':[r['pdf_page'] for r in records if r['changes']],'changes':sum(len(r['changes']) for r in records)})
 (dest/'frozen.json').write_text(json.dumps({'version':VERSION,'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scope':summary,'vlm_calls':0,'development_revision_not_independent_validation':True},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
