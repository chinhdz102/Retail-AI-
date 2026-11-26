"""Selection wire omits local bookkeeping without relaxing product validation."""
import json

import pytest
from test_customer_consultation import Model, product

from backend.services.customer_consultation import (
 ConsultationDecision,
 GroundedSelection,
 _render_selection,
 _structured,
 _tool_schema,
)
from backend.services.groq import GroqChatResult


@pytest.mark.parametrize('strict',[False,True])
def test_selection_wire_keeps_only_purchase_decision_fields(strict):
 schema=_tool_schema(GroundedSelection,strict=strict)
 pick=schema['properties']['picks']['items']
 assert set(pick['properties'])=={'candidate','quantity','search_index'}
 assert not {'basis','label_quote'} & set(pick.get('required',[]))
 assert pick['additionalProperties'] is False
 assert GroundedSelection.model_validate({'picks':[{'candidate':0}]}).picks[0].basis=='category'

@pytest.mark.asyncio
async def test_lean_tool_mock_restores_local_defaults_and_renders():
 model=Model({'picks':[{'candidate':0,'quantity':1,'search_index':0}],
              'text':'Mình chọn {name:0} ({price:0}) cho mức tiền ban đầu.'})
 selected,_=await _structured(model,GroundedSelection,'Select',{'customer':'Chọn giúp mình'})
 assert selected.picks[0].basis=='category' and selected.picks[0].label_quote==''
 row=product()
 text,products=_render_selection(selected,[row],ConsultationDecision(action='recommend'),{row.product_id:[0]})
 assert products==[row] and row.name in text

@pytest.mark.asyncio
async def test_native_structured_wire_omits_bookkeeping_and_rejects_unknown_fields():
 class Native:
  async def structured_chat(self,messages,**kwargs):
   pick_props = kwargs['schema']['properties']['picks']['items']['properties']
   assert set(pick_props) == {'candidate', 'quantity', 'search_index'}
   return GroqChatResult(json.dumps({'picks':[{'candidate':0,'invented':True}],
                                  'text':'Mình chọn loại này để dùng hằng ngày.'}), 'mock')
 with pytest.raises(ValueError,match='Extra inputs'):
  await _structured(Native(),GroundedSelection,'Select',{'customer':'Chọn giúp mình'})


def test_legacy_local_sugar_label_validation_is_not_removed():
 row=product(name='Sữa không đường Nova')
 selected = GroundedSelection.model_validate({
  'picks': [{'candidate': 0, 'basis': 'label', 'label_quote': 'trị mụn'}],
  'text': 'Mình chọn {name:0} ({price:0}) cho mức tiền ban đầu.',
 })
 with pytest.raises(ValueError,match='Unsupported label'):
  _render_selection(selected,[row],ConsultationDecision(action='recommend'),{row.product_id:[0]})
