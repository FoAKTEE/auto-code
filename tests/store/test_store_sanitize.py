"""Sanitize-before-hash (contracts §M): secrets never enter content-addressed storage."""
import hashlib
import os

import pytest

from zkqec.contracts import canonical_json, content_hash
from zkqec.store import ArtifactStore, Sanitizer, SecretDetected

# Deliberately secret-bearing values (synthetic; never real credentials).
SK_ANT = "sk-ant-api03-SYNTHETICsecretVALUE0123456789abcdefXYZ"
SK = "sk-proj-SYNTHETICopenaiKEY0123456789"
BEARER = "eyJhbGciOiJIUzI1NiJ9.SYNTHETICbearerTOKEN.abc123"
REFRESH = "rt-SYNTHETIC-refresh-0987654321"
COOKIE = "session=SYNTHETICcookie42; other=1"
SECRETS = (SK_ANT, SK, BEARER, REFRESH, "SYNTHETICcookie42", "SYNTHETICidtoken", "SYNTHETICapikey")


def secret_payload():
    return {
        "response": {"text": f"use key {SK_ANT} and {SK}", "usage": {"input_tokens": 12}},
        "headers": {"Authorization": f"Bearer {BEARER}", "Cookie": COOKIE, "x-request-id": "r1"},
        "oauth": {"access_token": "at-SYNTHETIC", "refresh_token": REFRESH,
                  "id_token": "SYNTHETICidtoken", "expires_in": 3600},
        "pairs": [["Authorization", f"Bearer {BEARER}"], ["Accept", "application/json"]],
        "raw": 'proxy log: {"api_key": "SYNTHETICapikey"} sent',
    }


def all_disk_bytes(root):
    out = b""
    for dirpath, _, files in os.walk(root):
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as fh:
                out += fh.read()
    return out


def test_secret_bearing_json_is_redacted_before_hashing(tmp_path):
    store = ArtifactStore(tmp_path)
    ing = store.ingest_json(secret_payload())
    stored = store.get_bytes(ing.sha)
    # the address is the hash of the *redacted* canonical bytes
    assert ing.sha == hashlib.sha256(stored).hexdigest()
    clean, _ = Sanitizer().sanitize_obj(secret_payload())
    assert stored == canonical_json(clean)
    assert ing.sha != content_hash(secret_payload())
    # no secret anywhere on disk (artifact, redaction record, index)
    disk = all_disk_bytes(tmp_path)
    for secret in SECRETS:
        assert secret.encode() not in disk, secret
    got = store.get_json(ing.sha)
    assert got["oauth"]["expires_in"] == 3600 and got["headers"]["x-request-id"] == "r1"
    assert got["headers"]["Authorization"] == "[REDACTED:secret_field]"
    assert got["pairs"][1] == ["Accept", "application/json"]
    assert "[REDACTED:anthropic_key]" in got["response"]["text"]
    assert "[REDACTED:sk_key]" in got["response"]["text"]


def test_redaction_record_names_rules_and_counts_never_secrets(tmp_path):
    store = ArtifactStore(tmp_path)
    ing = store.ingest_json(secret_payload())
    assert ing.redaction is not None
    records = store.redactions(ing.sha)
    assert len(records) == 1
    rec = records[0]
    assert rec["artifact"] == ing.sha and rec["rules"] == ing.redactions
    assert rec["rules"]["secret_field"] >= 5 and rec["rules"]["anthropic_key"] == 1
    assert rec["total"] == sum(rec["rules"].values())
    text = canonical_json(rec).decode()
    for secret in SECRETS:
        assert secret not in text


def test_secret_bearing_http_bytes_are_redacted(tmp_path):
    store = ArtifactStore(tmp_path)
    raw = (f"HTTP/1.1 200 OK\r\nSet-Cookie: {COOKIE}\r\nAuthorization: Bearer {BEARER}\r\n"
           f"X-Api-Key: SYNTHETICapikey\r\n\r\n"
           f'{{"access_token": "at-SYNTHETIC", "refresh_token": "{REFRESH}", "n": 7}}\n'
           f"GET /cb?id_token=SYNTHETICidtoken&x=1\n").encode()
    ing = store.ingest_bytes(raw, "http_response")
    body = store.get_bytes(ing.sha)
    assert ing.sha == hashlib.sha256(body).hexdigest()
    for secret in SECRETS + ("at-SYNTHETIC",):
        assert secret.encode() not in all_disk_bytes(tmp_path), secret
    assert b'"n": 7' in body and b"&x=1" in body
    assert set(ing.redactions) >= {"cookie_header", "auth_header", "secret_field", "secret_param"}


def test_binary_payload_with_secret_is_refused_not_stored(tmp_path):
    store = ArtifactStore(tmp_path)
    blob = b"\x00\x01\x02" + SK_ANT.encode() + b"\xff"
    with pytest.raises(SecretDetected):
        store.put_bytes(blob, "npy")
    assert SK_ANT.encode() not in all_disk_bytes(tmp_path)
    assert list(store.iter_shas()) == []


def test_sanitizer_is_idempotent_and_leaves_clean_payloads_alone(tmp_path):
    s = Sanitizer()
    once, counts = s.sanitize_obj(secret_payload())
    assert counts
    twice, again = s.sanitize_obj(once)
    assert twice == once and not again
    store = ArtifactStore(tmp_path)
    clean = {"hx": {"shape": [3, 7], "entries": [[0, 1], [2, 6]]}, "note": "bearer of good news"}
    ing = store.ingest_json(clean)
    assert ing.sha == content_hash(clean) and ing.redaction is None
    assert store.redactions(ing.sha) == []


# --- Codex M1 review #9: placeholder-prefix and escaped-key bypasses -------------------------------
LEAK = "actual-secret-12345678"


def test_placeholder_prefix_does_not_exempt_a_secret():
    s = Sanitizer()
    obj = {"api_key": f"[REDACTED:prefix] {LEAK}"}
    clean, counts = s.sanitize_obj(obj)
    assert LEAK not in canonical_json(clean).decode() and counts["secret_field"] == 1
    assert s.detect_obj(obj)                                   # residual detection, same rule
    for text in (f'{{"api_key":"[REDACTED:prefix] {LEAK}"}}',
                 f"Authorization: [REDACTED:auth_header] {LEAK}\n",
                 f"GET /cb?access_token=[REDACTED:secret_param]{LEAK}&x=1"):
        out, counts = s.sanitize_text(text)
        assert LEAK not in out and counts, text
    # exact placeholders are still left alone (idempotence)
    for done in ('{"api_key":"[REDACTED:secret_field]"}', "Cookie: [REDACTED:cookie_header]\n",
                 "x?access_token=[REDACTED:secret_param]&y=1"):
        assert s.sanitize_text(done) == (done, {})


def test_escaped_json_keys_are_redacted(tmp_path):
    raw = f'{{"\\u0061pi_key":"{LEAK}","n":1}}'.encode()
    store = ArtifactStore(tmp_path)
    ing = store.ingest_bytes(raw, "json")
    assert LEAK.encode() not in all_disk_bytes(tmp_path) and ing.redactions
    assert store.get_json(ing.sha)["n"] == 1
    s = Sanitizer()
    out, counts = s.sanitize_text(f'log line {{"Proxy\\u002dAuthorization": "{LEAK}"}}')
    assert LEAK not in out and counts["secret_field"] == 1
    # JSON nested inside a JSON string is decoded and sanitized structurally
    nested = canonical_json({"cli_stdout": f'{{"\\u0061pi_key":"{LEAK}"}}'})
    clean, counts = s.sanitize_bytes(nested, "json")
    assert LEAK.encode() not in clean and counts
    assert s.detect_obj({"x": f'{{"\\u0061pi_key":"{LEAK}"}}'})
