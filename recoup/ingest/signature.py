"""Razorpay webhook signature verification.

Razorpay signs the *exact bytes* it sent. That is why every function here
takes ``bytes`` and never a parsed dict: JSON round-tripping re-orders
keys and normalises whitespace, and a signature computed over a
re-serialised body will disagree with a valid one -- or, worse, agree with
a body that was modified in transit.

``hmac.compare_digest`` rather than ``==``: a plain comparison leaks how
many leading characters matched via its timing, which is enough to forge a
signature given enough attempts.
"""

import hashlib
import hmac

SIGNATURE_HEADER = "X-Razorpay-Signature"


def compute_signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(compute_signature(body, secret), signature.strip().lower())
