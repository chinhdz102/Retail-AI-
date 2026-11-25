"""Offline renderer replay of captured synthetic consultation decisions. No provider/network."""
import argparse
import hashlib
import json
from pathlib import Path

from backend.products.catalog import ProductSnapshot
from backend.services.customer_consultation import (
    ConsultationDecision, GroundedSelection, ConsultationReview,
    _render_selection, _review_issue,
)


def attempt(selection, candidates, plan, sources):
    try:
        text, products = _render_selection(selection.model_copy(deep=True), candidates, plan, sources)
        return {'status':'rendered','text':text,'product_ids':[p.product_id for p in products],
                'quality_note':'Renderer success verifies structural boundaries only; usefulness/claims require review.'}
    except ValueError as exc:
        return {'status':'validation_error','message':str(exc)}


def replay(path):
    report=json.loads(path.read_text(encoding='utf-8'))
    rows=[]
    for journey in report['rows']:
        context=None;selection=None
        for call in journey['calls']:
            if call['stage']=='GroundedSelection' and 'decision' in call:
                context=json.loads(call['context'][1]['content'].split('\n',1)[1])
                facts=context['candidates']
                candidates=[ProductSnapshot(product_id=f['id'],name=f['name'],category=f['type'],
                                             price_vnd=f['price_vnd'],stock=1,uom=f.get('uom')) for f in facts]
                sources={f['id']:f.get('search_indexes',[]) for f in facts}
                plan=ConsultationDecision.model_validate(context['decision'])
                selection=GroundedSelection.model_validate(call['decision'])
                rows.append({'kind':'captured_selection','result':attempt(selection,candidates,plan,sources)})
            elif call['stage']=='ConsultationReview' and 'decision' in call and context and selection:
                review=ConsultationReview.model_validate(call['decision'])
                item={'kind':'captured_review','acceptable':review.acceptable,
                      'review_issue':_review_issue(review,selection,candidates)}
                if review.revised_text:
                    revised=selection.model_copy(update={'text':review.revised_text})
                    item['revised_renderer']=attempt(revised,candidates,plan,sources)
                    item['publication_allowed']=not item['review_issue'] and item['revised_renderer']['status']=='rendered'
                rows.append(item)
    return {'source':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':rows}


def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--input',type=Path,action='append',required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    with args.output.open('x',encoding='utf-8') as out:
        json.dump({'kind':'offline captured-draft renderer replay','network_calls':0,
                   'reports':[replay(path) for path in args.input]},out,ensure_ascii=False,indent=2)

if __name__=='__main__':main()
