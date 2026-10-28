import hashlib
import hmac


def create_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(create_signature(secret, body), signature)
