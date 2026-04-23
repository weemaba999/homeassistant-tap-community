"""Tests for webhook signature verification.

Focuses on the pure `verify_signature()` function. The handler itself
(registration + HA event firing) is integration-level and covered in
CI with pytest-homeassistant-custom-component.
"""
from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from tapelectric.webhook import verify_signature


SECRET = "shh-its-a-secret"


def _sign(ts: str, body: str, secret: str = SECRET) -> str:
    payload = f"{ts}.{body}".encode("utf-8")
    return hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256,
    ).hexdigest().upper()


def test_verify_signature_happy_path():
    ts = str(int(time.time()))
    body = '{"event":"SessionStarted"}'
    sig = _sign(ts, body)
    assert verify_signature(SECRET, ts, body, sig) is True


def test_verify_signature_lowercase_still_accepted():
    """compare_digest is case-insensitive? We compare with .upper() on
    both sides, so lowercase input should still verify."""
    ts = str(int(time.time()))
    body = '{"x":1}'
    sig = _sign(ts, body).lower()
    assert verify_signature(SECRET, ts, body, sig) is True


def test_verify_signature_wrong_secret():
    ts = str(int(time.time()))
    body = '{"x":1}'
    sig = _sign(ts, body, secret="other-secret")
    assert verify_signature(SECRET, ts, body, sig) is False


def test_verify_signature_tampered_body():
    ts = str(int(time.time()))
    original = '{"event":"SessionStarted"}'
    tampered = '{"event":"SessionEnded"}'   # attacker swap
    sig = _sign(ts, original)
    assert verify_signature(SECRET, ts, tampered, sig) is False


def test_verify_signature_rejects_stale_timestamp():
    stale = str(int(time.time()) - 10_000)   # way past the 300s window
    body = '{"x":1}'
    sig = _sign(stale, body)
    assert verify_signature(SECRET, stale, body, sig) is False


def test_verify_signature_rejects_future_timestamp():
    future = str(int(time.time()) + 10_000)
    body = '{"x":1}'
    sig = _sign(future, body)
    assert verify_signature(SECRET, future, body, sig) is False


@pytest.mark.parametrize("missing", ["secret", "timestamp", "body", "signature"])
def test_verify_signature_missing_pieces_fail(missing):
    ts = str(int(time.time()))
    body = '{"x":1}'
    sig = _sign(ts, body)
    kwargs = dict(secret=SECRET, timestamp=ts, raw_body=body, provided=sig)
    kwargs[{"secret": "secret", "timestamp": "timestamp",
            "body": "raw_body", "signature": "provided"}[missing]] = ""
    assert verify_signature(**kwargs) is False


def test_verify_signature_non_numeric_timestamp():
    assert verify_signature(SECRET, "not-a-number", "body", "sig") is False
