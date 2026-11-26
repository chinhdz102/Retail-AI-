"""Negative editorial suggestions need a bounded independent final-text check."""
import pytest
from test_customer_consultation import Model, Repository, product

from backend.services.customer_consultation import (
    ConsultationDecision,
    ConsultationReview,
    GroundedSelection,
    _editorial_proposal,
    consult_customer,
)
from backend.services.sessions import InMemorySessionStore


def check(row, **overrides):
    return {'candidate':0,'name_quote':row.name,'actual_type':'Sổ',
            'fits_need':True,'claims_supported':False,'issue':'Unproven benefit',**overrides}


def inputs():
    row=product(name='Sổ Nova A5',price=20000,path='Cửa hàng > Văn phòng > Sổ')
    plan={'action':'recommend','need':'Sổ dùng hằng ngày','searches':[{'category':'Sổ'}]}
    selected = {
        'picks': [{'candidate': 0}],
        'text': 'Mình chọn {name:0} ({price:0}) vì dùng bền nhiều năm.',
    }
    revised='Mình ưu tiên {name:0} ({price:0}), khổ A5 theo tên sản phẩm.'
    return row,plan,selected,revised


@pytest.mark.asyncio
async def test_rejected_revision_requires_fresh_check_and_uses_four_calls():
    row,plan,selected,revised=inputs()
    model=Model(plan,selected,{'acceptable':False,'issue':'Unsupported durability',
                             'product_checks':[check(row)],'revised_text':revised},
                {'acceptable':True})
    result=await consult_customer(text='Chọn giúp mình sổ dùng hằng ngày',
        record=InMemorySessionStore().create('editor'),repository=Repository([row]),groq=model)
    assert result.products==[row] and 'nhiều năm' not in result.text
    assert len(model.calls)==4
    final_context=model.calls[-1][0][1]['content']
    assert 'Unsupported durability' not in final_context
    assert 'planner_guidance' not in final_context


@pytest.mark.asyncio
async def test_fresh_rejection_cannot_leak_negative_revision_or_select_again():
    row,plan,selected,revised=inputs()
    model=Model(plan,selected,{'acceptable':False,'product_checks':[check(row)],'revised_text':revised},
                {'acceptable':False,'issue':'Still unsafe','product_checks':[check(row)]})
    with pytest.raises(ValueError,match='No acceptable consultation reply'):
        await consult_customer(text='Chọn giúp mình sổ dùng hằng ngày',
            record=InMemorySessionStore().create('editor'),repository=Repository([row]),groq=model)
    assert len(model.calls)==4


@pytest.mark.asyncio
async def test_failed_fresh_review_propagates_without_unreviewed_result():
    from unittest.mock import AsyncMock, patch
    row,plan,selected,revised=inputs()
    model=Model(plan,selected,{'acceptable':False,'product_checks':[check(row)],'revised_text':revised})
    with patch('backend.services.customer_consultation._review_editorial_proposal',
               new=AsyncMock(side_effect=TimeoutError)):
        with pytest.raises(TimeoutError):
            await consult_customer(text='Chọn giúp mình sổ dùng hằng ngày',
                record=InMemorySessionStore().create('editor'),repository=Repository([row]),groq=model)


@pytest.mark.asyncio
async def test_last_rejected_revision_can_be_checked_without_publishing_rejected_draft():
    row, plan, selected, revised = inputs()
    rejection = {'acceptable': False, 'product_checks': [check(row)]}
    model = Model(plan, selected, rejection, selected,
                  {**rejection, 'revised_text': revised}, {'acceptable': True})
    result = await consult_customer(
        text='Chọn giúp mình sổ dùng hằng ngày',
        record=InMemorySessionStore().create('final-editor'),
        repository=Repository([row]), groq=model,
    )
    assert result.products == [row]
    assert 'nhiều năm' not in result.text
    assert len(model.calls) == 6


@pytest.mark.parametrize('text',[
    'Mình chọn {name:1} ({price:1}).',
    'Mình chọn {name:0} ({price:0}). Bạn thích hãng nào?',
    'Mình chọn {name:0} với giá 1.000đ.',
])
def test_editorial_proposal_rejects_changed_identity_question_or_price(text):
    row,plan,selected,_=inputs()
    review=ConsultationReview(acceptable=False,product_checks=[check(row)],revised_text=text)
    assert (
        _editorial_proposal(
            review,
            GroundedSelection.model_validate(selected),
            [row],
            ConsultationDecision.model_validate(plan),
            {row.product_id: [0]},
        )
        is None
    )


def test_wrong_function_does_not_enter_editorial_repair():
    row,plan,selected,revised=inputs()
    review = ConsultationReview(
        acceptable=False,
        product_checks=[check(row, fits_need=False)],
        revised_text=revised,
    )
    assert (
        _editorial_proposal(
            review,
            GroundedSelection.model_validate(selected),
            [row],
            ConsultationDecision.model_validate(plan),
            {row.product_id: [0]},
        )
        is None
    )


def test_overbudget_decision_is_not_repaired_by_prose():
    row,plan,selected,revised=inputs()
    plan.update(basket=True,total_budget_vnd=1000)
    selected['picks']=[{'candidate':0,'search_index':0}]
    review=ConsultationReview(acceptable=False,product_checks=[check(row)],revised_text=revised)
    assert (
        _editorial_proposal(
            review,
            GroundedSelection.model_validate(selected),
            [row],
            ConsultationDecision.model_validate(plan),
            {row.product_id: [0]},
        )
        is None
    )
