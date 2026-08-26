import hashlib
import hmac

from recoup.ingest.signature import SIGNATURE_HEADER, compute_signature, verify_signature

SECRET = "webhook_value"
BODY = b'{"event":"subscription.pending","payload":{}}'


def expected(body: bytes = BODY, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_the_signature_is_hmac_sha256_hex_over_the_raw_body():
    assert compute_signature(BODY, SECRET) == expected()


def test_a_correct_signature_verifies():
    assert verify_signature(BODY, expected(), SECRET) is True


def test_a_tampered_body_does_not_verify():
    assert verify_signature(BODY + b" ", expected(), SECRET) is False


def test_a_signature_from_the_wrong_secret_does_not_verify():
    assert verify_signature(BODY, expected(secret="attacker"), SECRET) is False


def test_a_missing_signature_does_not_verify():
    assert verify_signature(BODY, None, SECRET) is False


def test_an_empty_signature_does_not_verify():
    assert verify_signature(BODY, "", SECRET) is False


def test_a_malformed_signature_does_not_verify_and_does_not_raise():
    assert verify_signature(BODY, "not-hex-at-all", SECRET) is False


def test_signature_comparison_is_case_insensitive_on_the_hex():
    assert verify_signature(BODY, expected().upper(), SECRET) is True


def test_reordering_json_keys_changes_the_signature():
    assert compute_signature(b'{"a":1,"b":2}', SECRET) != compute_signature(b'{"b":2,"a":1}', SECRET)


def test_the_header_name_is_the_one_razorpay_sends():
    assert SIGNATURE_HEADER == "X-Razorpay-Signature"
