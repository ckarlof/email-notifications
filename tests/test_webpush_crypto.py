"""Unit tests for RFC 8291 / RFC 8188 Web Push cryptography."""

from __future__ import annotations

import json

from custom_components.boardtogether.webpush_crypto import (
    WebPushClientKeys,
    b64url_decode,
    b64url_encode,
    decrypt_webpush_payload,
    encrypt_aes128gcm,
)


def test_client_key_generation_and_serialization() -> None:
    """Test P-256 ECDH key generation and dictionary roundtrip."""
    keys = WebPushClientKeys.generate()
    assert len(b64url_decode(keys.p256dh)) == 65
    assert b64url_decode(keys.p256dh)[0] == 0x04
    assert len(b64url_decode(keys.auth)) == 16
    assert len(b64url_decode(keys.private_key_b64)) == 32

    restored = WebPushClientKeys.from_dict(keys.to_dict())
    assert restored == keys


def test_aes128gcm_encrypt_decrypt_roundtrip() -> None:
    """Test encrypting and decrypting a BoardTogether IsYourTurnNotification payload."""
    keys = WebPushClientKeys.generate()
    notification = {
        "id": "notif-12345",
        "type": "user",
        "action": "isYourTurn",
        "data": {
            "user": {"id": "user-1"},
            "game": {
                "id": "game-abc",
                "typeId": "indonesia",
                "name": "Friday Night Indonesia",
            },
        },
    }
    plaintext = json.dumps(notification).encode("utf-8")
    encrypted = encrypt_aes128gcm(plaintext, keys.p256dh, keys.auth)
    data_b64 = b64url_encode(encrypted)

    decrypted = decrypt_webpush_payload(
        data_b64=data_b64,
        keys=keys,
        headers={"encoding": "aes128gcm"},
    )
    assert json.loads(decrypted.decode("utf-8")) == notification
