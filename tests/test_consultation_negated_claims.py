"""Evidence warnings must not become false claims or authorize other promises."""
import pytest

from backend.services.customer_consultation import ConsultationDecision, ConsultationReply, _render


@pytest.mark.parametrize('text',[
 'Không thể khẳng định loại này không gây kích ứng; nên xem thành phần trên nhãn.',
 'Không thể suy ra sản phẩm giúp đủ no hay đủ chất từ tên gọi.',
 'Không có cơ sở nói sản phẩm giặt sạch hơn chỉ từ thương hiệu.',
])
def test_narrow_evidence_warning_allowed(text):
 assert _render(ConsultationReply(text=text),[],ConsultationDecision(action='advise'))[0]==text

@pytest.mark.parametrize('text',[
 'Loại này không gây kích ứng.',
 'Món này giúp đủ no và đủ chất.',
 'Sản phẩm giặt sạch hơn.',
 'Không thể khẳng định đủ no, nhưng sản phẩm giặt sạch hơn.',
 'Không có cơ sở nói sản phẩm giặt sạch hơn; loại này không gây kích ứng.',
 'Không thể khẳng định đủ no và loại này không gây kích ứng.',
 'Không thể khẳng định đủ no vì loại này giặt sạch hơn.',
 'Không thể khẳng định đủ no. Loại này đủ chất.',
 'Không thể khẳng định đủ no nhưng sản phẩm an toàn cho mèo.',
 'Không thể khẳng định đủ no nhưng sản phẩm điều trị mụn.',
 'Sản phẩm an toàn cho mèo.',
 'Sản phẩm điều trị mụn.',
 'Không thể khẳng định không phải sản phẩm giặt sạch hơn.',
])
def test_affirmative_claims_and_mixed_scopes_remain_rejected(text):
 with pytest.raises(ValueError,match='Unsupported'):
  _render(
   ConsultationReply(text=text + ' Bạn có thể xem quy cách.'),
   [],
   ConsultationDecision(action='advise'),
  )


@pytest.mark.parametrize('condition', ['mụn', 'viêm da'])
def test_general_care_alongside_treatment_is_not_a_product_efficacy_promise(condition):
 text=f'Chăm sóc thông thường đi kèm điều trị {condition} nhưng không thay thế được điều trị.'
 assert _render(ConsultationReply(text=text),[],ConsultationDecision(action='advise'))[0]==text


@pytest.mark.parametrize('text', [
 'Chăm sóc đi kèm điều trị; sản phẩm này giúp điều trị mụn.',
 'Trong quá trình điều trị, loại này giúp giảm viêm.',
])
def test_nominal_treatment_reference_does_not_authorize_a_later_efficacy_claim(text):
 with pytest.raises(ValueError,match='Unsupported'):
  _render(ConsultationReply(text=text),[],ConsultationDecision(action='advise'))
