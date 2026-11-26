"""An observed instant-food class is not a preparation or readiness certificate."""
import pytest

from backend.services.customer_consultation import _assert_no_unverified_immediate_use

OBS=[('Phở bò ăn liền Đệ Nhất gói 65g','Phở - Bún','Thực phẩm > Phở - Bún')]

@pytest.mark.parametrize('sentence',[
 'Mình chọn món này vì là phở ăn liền, quy cách gói nhỏ.',
 'Đây là loại phở ăn liền, hãy xem hướng dẫn chuẩn bị trên bao bì.',
])
def test_observed_type_noun_is_not_readiness(sentence):
 _assert_no_unverified_immediate_use(sentence,observations=OBS)

@pytest.mark.parametrize('sentence',[
 'Món này là phở ăn liền, mở ra ăn ngay.',
 'Món này là phở ăn liền, chỉ cần đổ nước sôi là có bữa sáng.',
 'Món này là phở ăn liền, chỉ việc hâm lại rồi ăn.',
 'Bạn có thể ăn liền món này.',
 'Đây là loại bánh ăn liền, quy cách gói nhỏ.',
 'Đây là ghế đóng hộp, mở ra dùng ngay.',
 'Đây là ghế nguyên bộ, không cần lắp ráp.',
 'Đây là phở ăn liền, chỉ cần nước sôi là xong.',
 'Chỉ cần nước nóng là có bữa sáng.',
])
def test_observation_does_not_authorize_other_readiness_or_recipe(sentence):
 with pytest.raises(ValueError):
  _assert_no_unverified_immediate_use(sentence,observations=OBS)

@pytest.mark.parametrize('sentence',[
 'Đừng mặc định đóng hộp là mở ra ăn ngay.',
 'Không thể khẳng định ghế đóng hộp là mở ra dùng ngay.',
 'Nếu trên nhãn ghi ăn ngay thì kiểm tra thêm hướng dẫn bảo quản.',
 'Bạn có thể trộn ngũ cốc với sữa theo hướng dẫn trên nhãn.',
])
def test_conditional_warnings_and_normal_preparation_still_allowed(sentence):
 _assert_no_unverified_immediate_use(sentence,observations=OBS)


def test_renderer_uses_selected_catalog_observations_without_readiness_inference():
 from backend.products.catalog import ProductSnapshot
 from backend.services.customer_consultation import ConsultationDecision, ConsultationReply, _render
 row=ProductSnapshot(product_id='synthetic-instant',name=OBS[0][0],category=OBS[0][1],price_vnd=10000,stock=1)
 reply = ConsultationReply(
  text='Mình chọn {name:0} vì là phở ăn liền, quy cách gói nhỏ.',
  picks=[{'candidate': 0}],
 )
 text,products=_render(reply,[row],ConsultationDecision(action='recommend'))
 assert row.name in text and products==[row]
 with pytest.raises(ValueError,match='preparation sufficiency'):
  _render(
   reply.model_copy(
    update={'text': 'Mình chọn {name:0} vì là phở ăn liền, chỉ cần đổ nước sôi.'}
   ),
   [row],
   ConsultationDecision(action='recommend'),
  )


@pytest.mark.parametrize('claim', [
 'Chỉ cần hâm nóng với nước hoặc sữa trong 2‑3 phút.',
 'Pha với nước nóng và chờ vài phút.',
 'Gói lớn dùng được lâu.',
])
def test_unverified_time_and_longevity_cannot_be_published(claim):
 from backend.products.catalog import ProductSnapshot
 from backend.services.customer_consultation import ConsultationDecision, ConsultationReply, _render
 row=ProductSnapshot(product_id='timing',name=OBS[0][0],category=OBS[0][1],price_vnd=10000,stock=1)
 with pytest.raises(ValueError,match='Unsupported'):
  _render(ConsultationReply(text='Mình chọn {name:0}. '+claim,picks=[{'candidate':0}]),
          [row],ConsultationDecision(action='recommend'))
