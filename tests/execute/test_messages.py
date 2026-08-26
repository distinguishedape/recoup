import pytest

from recoup.execute.messages import ALLOWED_TEMPLATE_IDS, TEMPLATES, render
from recoup.models.enums import Tier

CONTEXT = {"customer_id": "cust_1", "amount_inr": "999.00", "update_link": "https://x.test/u"}


def test_the_allowlist_is_exactly_the_defined_templates():
    assert ALLOWED_TEMPLATE_IDS == frozenset(TEMPLATES)


def test_the_templates_the_planners_reference_all_exist():
    for template_id in [
        "t1_notify_email",
        "t2_update_instrument_email",
        "t2_update_instrument_sms",
        "t3_final_notice_email",
        "t3_final_notice_sms",
    ]:
        assert template_id in TEMPLATES


def test_each_template_declares_the_tier_and_channel_it_belongs_to():
    assert TEMPLATES["t1_notify_email"].tier is Tier.T1_NOTIFY
    assert TEMPLATES["t1_notify_email"].channel == "email"
    assert TEMPLATES["t3_final_notice_sms"].tier is Tier.T3_FINAL_NOTICE
    assert TEMPLATES["t3_final_notice_sms"].channel == "sms"


def test_rendering_substitutes_the_context():
    message = render("t1_notify_email", CONTEXT)
    assert "999.00" in message.body
    assert "{" not in message.body


def test_rendering_an_unknown_template_is_refused():
    with pytest.raises(KeyError):
        render("t9_whatever_i_feel_like", CONTEXT)


def test_a_missing_context_value_is_a_loud_error_not_a_literal_brace():
    with pytest.raises(ValueError):
        render("t2_update_instrument_email", {"customer_id": "cust_1"})


def test_sms_templates_are_short_enough_to_actually_send():
    for template in TEMPLATES.values():
        if template.channel == "sms":
            assert len(template.body.format(**CONTEXT)) <= 320


def test_no_template_promises_anything_about_a_customers_account_status():
    for template in TEMPLATES.values():
        lowered = template.body.lower()
        assert "guarantee" not in lowered
        assert "legal action" not in lowered
