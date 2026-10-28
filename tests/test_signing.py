from app.signing import create_signature, verify_signature


def test_signature_can_be_verified():
    body = b'{"event":"demo.created"}'
    signature = create_signature("demo-secret", body)

    assert signature.startswith("sha256=")
    assert verify_signature("demo-secret", body, signature)


def test_signature_fails_for_wrong_secret_or_modified_body():
    body = b'{"event":"demo.created"}'
    signature = create_signature("demo-secret", body)

    assert not verify_signature("other-secret", body, signature)
    assert not verify_signature("demo-secret", body + b" ", signature)
