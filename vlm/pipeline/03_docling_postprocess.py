"""Conservative layout routing. Decisions are hypotheses, not verified accuracy."""
import argparse,json,re,hashlib,html
from pathlib import Path
import pymupdf
from PIL import Image,ImageDraw
VERSION='docling-context-routing/2'
CHARTS={'bar_chart','line_chart','pie_chart','scatter_plot','box_plot','table','other_chart','flow_chart'}
DECOR={'logo','icon','signature','qr_code','bar_code','stamp'}
def rect(prov,h):
 b=prov['bbox'];return [b['l'],h-b['t'],b['r'],h-b['b']] if b.get('coord_origin')=='BOTTOMLEFT' else [b['l'],b['t'],b['r'],b['b']]
def area(b):return max(0,b[2]-b[0])*max(0,b[3]-b[1])
def contained(a,b):return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))/max(1,area(a))
def value_signal(t):
 s=re.sub(r'\s+','',t)
 if re.fullmatch(r'(?:19|20)\d{2}(?:[.년/-]\d{1,2})*(?:[.월/-]\d{1,2})?[.일]?',s):return False
 return bool(re.search(r'\d',s) and (re.fullmatch(r'(?:개|명|건|조원|억원|백만원|천원|원)?[+-]?\d[\d,.]*(?:%|개|명|건|조원|억원|백만원|천원|원)?',s) or re.fullmatch(r'(?:세계|국내|글로벌)?\d[\d,.]*위',s)))
def process(data,pn,w,h):
 items=[]
 for kind in ['texts','tables','pictures']:
  for x in data.get(kind,[]):
   for p in x.get('prov',[]):
    if int(p['page_no'])==pn:
     cls=x.get('meta',{}).get('classification',{}).get('predictions',[])
     if not cls:
      for ann in x.get('annotations',[]):
       if ann.get('kind')=='classification':cls=ann.get('predicted_classes',ann.get('predictions',[]))
     cls=sorted(cls,key=lambda v:v.get('confidence',0),reverse=True)
     items.append(dict(ref=x.get('self_ref'),kind=kind,bbox=rect(p,h),text=x.get('text',''),label=x.get('label'),parent=x.get('parent'),classification=cls[:2],table_data=x.get('data',{}),captions=x.get('captions',[])))
 texts=[x for x in items if x['kind']=='texts' and x['label'] not in ['page_header','page_footer']];out=[]
 def add(x,decision,reason,bbox=None,refs=None):
  b=bbox or x['bbox'];b=[max(0,b[0]),max(0,b[1]),min(w,b[2]),min(h,b[3])]
  if b[2]<=b[0] or b[3]<=b[1]:return
  out.append(dict(bbox=b,decision=decision,reason=reason,source_refs=refs or [x['ref']],classification=x['classification'],verification_status='unverified'))
 for x in items:
  if x['kind']=='tables':
   cells=x['table_data'].get('table_cells',[]);signals=[]
   if not cells:signals.append('table_cells_unavailable')
   if any(c.get('row_span',1)>1 or c.get('col_span',1)>1 for c in cells):signals.append('merged_structure_not_confirmed_error')
   if any(c.get('column_header') and not str(c.get('text','')).strip() for c in cells):signals.append('empty_header')
   add(x,'review_required' if signals else 'parser_only',signals or ['structured_table_preserved_not_content_verified'])
  if x['kind']!='pictures':continue
  top=x['classification'][0] if x['classification'] else {};label=top.get('class_name',top.get('predicted_class',''));conf=top.get('confidence',0);inside=[t for t in texts if contained(t['bbox'],x['bbox'])>=.6];quant=any(value_signal(t['text']) for t in inside);caption_refs={v.get('$ref') for v in x['captions']};captions=[t for t in texts if t['ref'] in caption_refs];caption_quant=any(value_signal(t['text']) for t in captions)
  if label=='photograph' and conf>=.9 and not quant and not caption_quant:add(x,'exclude_vlm',['high_photo_score_no_detected_quantitative_signal_preserve_ocr'])
  elif label in DECOR and conf>=.9 and area(x['bbox'])/(w*h)<.01 and not inside and not captions:add(x,'exclude_vlm',['small_isolated_decoration_preserve_source'])
  elif label in CHARTS and conf>=.6:add(x,'vlm_candidate',['quantitative_or_flow_picture'])
  else:add(x,'review_required',['picture_type_or_need_uncertain'])
 # Numeric context proposals keep complete OCR strings; do not invent item/value links.
 # Small aligned graphical connectors may belong to a flow diagram. Keep their context.
 small_pics=[x for x in items if x['kind']=='pictures' and area(x['bbox'])/(w*h)<.01]
 grouped=set()
 for x in small_pics:
  b=x['bbox'];cy=(b[1]+b[3])/2
  peers=[y for y in small_pics if abs((y['bbox'][1]+y['bbox'][3])/2-cy)<.025*h]
  if len(peers)<2:continue
  left=min(y['bbox'][0] for y in peers);right=max(y['bbox'][2] for y in peers)
  if right-left<.15*w:continue
  refs=tuple(sorted(y['ref'] for y in peers))
  if refs in grouped:continue
  grouped.add(refs)
  near=[t for t in texts if left-.2*w<=(t['bbox'][0]+t['bbox'][2])/2<=right+.2*w and abs((t['bbox'][1]+t['bbox'][3])/2-cy)<.065*h]
  boxes=[y['bbox'] for y in peers]+[t['bbox'] for t in near]
  u=[min(q[0] for q in boxes)-4,min(q[1] for q in boxes)-4,max(q[2] for q in boxes)+4,max(q[3] for q in boxes)+4]
  out=[v for v in out if not (v['decision']=='exclude_vlm' and contained(v['bbox'],u)>.8)]
  add(x,'review_required',['aligned_small_graphics_and_neighbor_text_possible_diagram'],u,list(refs)+[t['ref'] for t in near])
 anchors=[t for t in texts if value_signal(t['text']) and len(t['text'])<=55 and (not re.fullmatch(r'\d+',t['text'].strip()) or t['bbox'][3]-t['bbox'][1]>=max(14,.018*h)) and not any(contained(t['bbox'],x['bbox'])>=.8 for x in out if x['decision']!='exclude_vlm')]
 for t in anchors:
  b=t['bbox'];cx=(b[0]+b[2])/2;cy=(b[1]+b[3])/2;u=[cx-.12*w,b[1]-.09*h,cx+.12*w,b[3]+.09*h]
  for other in anchors:
   if other is t:continue
   q=other['bbox'];ox=(q[0]+q[2])/2;oy=(q[1]+q[3])/2
   if abs(oy-cy)<max(b[3]-b[1],q[3]-q[1])*1.2:
    if ox<cx:u[0]=max(u[0],(ox+cx)/2)
    elif ox>cx:u[2]=min(u[2],(ox+cx)/2)
  add(t,'vlm_candidate' if b[3]-b[1]>=max(18,.025*h) else 'review_required',['numeric_context_window_no_semantic_link_inferred'],u)
 # Remove near-contained overlapping proposals of the same decision, never merge separate panels.
 keep=[]
 for i,x in enumerate(out):
  if any(i!=j and x['decision']==y['decision'] and contained(x['bbox'],y['bbox'])>.95 and (area(y['bbox'])>area(x['bbox']) or (area(y['bbox'])==area(x['bbox']) and j<i)) for j,y in enumerate(out)):continue
  x['region_id']=f'p{pn:04d}_r{len(keep)+1:03d}'
  b=x['bbox'];cx=(b[0]+b[2])/2
  headings=[t for t in texts if t['label']=='section_header' and t['bbox'][3]<=b[1] and b[1]-t['bbox'][3]<.25*h and t['bbox'][0]<=cx and cx-t['bbox'][0]<.5*w]
  if headings:
   t=min(headings,key=lambda q:b[1]-q['bbox'][3]);x['context']=[dict(text=t['text'],bbox=t['bbox'],source_ref=t['ref'])]
  keep.append(x)
 return keep

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);a=ap.parse_args();cp=Path(a.config).resolve();root=cp.parent;cfg=json.loads(cp.read_text(encoding='utf-8'));report=[]
 for spec in cfg['documents']:
  pdf=(root/spec['pdf']).resolve();sha=hashlib.sha256(pdf.read_bytes()).hexdigest();folder=root/cfg['output']/spec['id'];raw=folder/'docling';records=[]
  with pymupdf.open(pdf) as doc:
   for pn in spec['review_pages']:
    inp=raw/f'p{pn:03d}.json'
    if not inp.exists():records.append(dict(pdf_page=pn,status='missing_docling_output'));continue
    data=json.loads(inp.read_text(encoding='utf-8'));p=doc[pn-1];w,h=p.rect.width,p.rect.height
    regions=process(data,pn,w,h)
    for x in regions:x.update(doc_id='doc_'+sha[:16],version_sha256=sha,pdf_page=pn,rule_version=VERSION,source_locator=dict(type='pdf_page_bbox',pdf_page=pn,bbox=x['bbox'],units='pt',coordinate_space='pymupdf_unrotated_page'))
    record=dict(pdf_page=pn,status='processed',regions=regions,source_json=str(inp.relative_to(root)),rule_version=VERSION);records.append(record)
    pix=p.get_pixmap(matrix=pymupdf.Matrix(1,1),colorspace=pymupdf.csRGB,alpha=False);im=Image.frombytes('RGB',[pix.width,pix.height],pix.samples);draw=ImageDraw.Draw(im)
    colors=dict(vlm_candidate='red',review_required='orange',exclude_vlm='green',parser_only='blue')
    for x in regions:draw.rectangle(x['bbox'],outline=colors[x['decision']],width=2)
    (folder/'images').mkdir(exist_ok=True);im.save(folder/'images'/f'p{pn:03d}_docling_python.png')
  (folder/'docling_python.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8');report.append(dict(document=spec['id'],processed=sum(x['status']=='processed' for x in records),missing=sum(x['status']!='processed' for x in records)))
 (root/cfg['output']/'docling_routing_summary.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':main()
