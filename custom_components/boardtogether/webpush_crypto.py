"""RFC 8291 / RFC 8188 Web Push client cryptography using Python's cryptography library."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import os
import re
import struct
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def b64url_encode(data: bytes) -> str:
    """Encode bytes to unpadded base64url string."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    """Decode padded or unpadded base64url/base64 string to bytes."""
    cleaned = data.strip().replace("-", "+").replace("_", "/")
    padding = "=" * ((4 - (len(cleaned) % 4)) % 4)
    return base64.b64decode(cleaned + padding)


@dataclass(frozen=True)
class WebPushClientKeys:
    """Client P-256 ECDH keypair and 16-byte auth secret for Web Push."""

    private_key_b64: str
    p256dh: str
    auth: str

    @classmethod
    def generate(cls) -> WebPushClientKeys:
        """Generate a new P-256 keypair and 16-byte auth secret."""
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_num = private_key.private_numbers().private_value
        private_bytes = private_num.to_bytes(32, "big")
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        auth_bytes = os.urandom(16)
        return cls(
            private_key_b64=b64url_encode(private_bytes),
            p256dh=b64url_encode(public_bytes),
            auth=b64url_encode(auth_bytes),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WebPushClientKeys:
        """Restore keys from persisted dictionary."""
        return cls(
            private_key_b64=str(data["private_key_b64"]),
            p256dh=str(data["p256dh"]),
            auth=str(data["auth"]),
        )

    def to_dict(self) -> dict[str, str]:
        """Serialize keys for storage."""
        return {
            "private_key_b64": self.private_key_b64,
            "p256dh": self.p256dh,
            "auth": self.auth,
        }

    def load_private_key(self) -> ec.EllipticCurvePrivateKey:
        """Reconstruct the EllipticCurvePrivateKey instance."""
        private_int = int.from_bytes(b64url_decode(self.private_key_b64), "big")
        return ec.derive_private_key(private_int, ec.SECP256R1())

    @property
    def public_bytes(self) -> bytes:
        """Return 65-byte uncompressed public point."""
        return b64url_decode(self.p256dh)

    @property
    def auth_bytes(self) -> bytes:
        """Return 16-byte auth secret."""
        return b64url_decode(self.auth)


def _hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """Derive bytes using HKDF-SHA256."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
    ).derive(ikm)


def _xor_nonce(nonce: bytes, seq: int) -> bytes:
    """XOR 12-byte base nonce with 96-bit sequence number."""
    nonce_int = int.from_bytes(nonce, "big") ^ seq
    return nonce_int.to_bytes(12, "big")


def decrypt_aes128gcm(body: bytes, keys: WebPushClientKeys) -> bytes:
    """Decrypt an RFC 8291 / RFC 8188 aes128gcm Web Push payload."""
    if len(body) < 21:
        raise ValueError("Payload too short for aes128gcm header")

    salt = body[:16]
    (rs,) = struct.unpack(">I", body[16:20])
    idlen = body[20]

    if rs < 18:
        raise ValueError(f"Invalid record size in aes128gcm header: {rs}")
    if len(body) < 21 + idlen + 17:
        raise ValueError("Payload truncated before ciphertext")

    as_public_bytes = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]

    ua_private = keys.load_private_key()
    ua_public_bytes = keys.public_bytes
    as_public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), as_public_bytes
    )
    shared_secret = ua_private.exchange(ec.ECDH(), as_public)

    key_info = b"WebPush: info\x00" + ua_public_bytes + as_public_bytes
    ikm = _hkdf_sha256(
        ikm=shared_secret,
        salt=keys.auth_bytes,
        info=key_info,
        length=32,
    )

    cek = _hkdf_sha256(
        ikm=ikm,
        salt=salt,
        info=b"Content-Encoding: aes128gcm\x00",
        length=16,
    )
    base_nonce = _hkdf_sha256(
        ikm=ikm,
        salt=salt,
        info=b"Content-Encoding: nonce\x00",
        length=12,
    )

    aesgcm = AESGCM(cek)
    chunks: list[bytes] = []
    offset = 0
    seq = 0
    total_len = len(ciphertext)

    while offset < total_len:
        end = min(offset + rs, total_len)
        record = ciphertext[offset:end]
        is_last = end == total_len
        record_nonce = _xor_nonce(base_nonce, seq)
        decrypted = aesgcm.decrypt(record_nonce, record, None)

        # Strip trailing 0x00 padding bytes
        unpadded = decrypted.rstrip(b"\x00")
        if not unpadded:
            raise ValueError("Missing record delimiter in aes128gcm record")

        delimiter = unpadded[-1]
        expected_delimiter = 0x02 if is_last else 0x01
        if delimiter != expected_delimiter:
            raise ValueError(
                f"Invalid record delimiter 0x{delimiter:02x} (expected 0x{expected_delimiter:02x})"
            )

        chunks.append(unpadded[:-1])
        offset = end
        seq += 1

    return b"".join(chunks)


def decrypt_aesgcm(
    body: bytes,
    keys: WebPushClientKeys,
    encryption_header: str,
    crypto_key_header: str,
) -> bytes:
    """Decrypt legacy draft-ietf-webpush-encryption-04 (aesgcm) payload."""
    salt_match = re.search(r"salt=([A-Za-z0-9_\-]+)", encryption_header)
    dh_match = re.search(r"dh=([A-Za-z0-9_\-]+)", crypto_key_header)
    if not salt_match or not dh_match:
        raise ValueError("Missing salt or dh parameter in aesgcm headers")

    salt = b64url_decode(salt_match.group(1))
    as_public_bytes = b64url_decode(dh_match.group(1))
    ua_public_bytes = keys.public_bytes

    ua_private = keys.load_private_key()
    as_public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), as_public_bytes
    )
    shared_secret = ua_private.exchange(ec.ECDH(), as_public)

    ikm = _hkdf_sha256(
        ikm=shared_secret,
        salt=keys.auth_bytes,
        info=b"Content-Encoding: auth\x00",
        length=32,
    )

    context = (
        b"P-256\x00"
        + struct.pack(">H", len(ua_public_bytes))
        + ua_public_bytes
        + struct.pack(">H", len(as_public_bytes))
        + as_public_bytes
    )
    cek_info = b"Content-Encoding: aesgcm\x00" + context
    nonce_info = b"Content-Encoding: nonce\x00" + context

    cek = _hkdf_sha256(ikm=ikm, salt=salt, info=cek_info, length=16)
    nonce = _hkdf_sha256(ikm=ikm, salt=salt, info=nonce_info, length=12)

    decrypted = AESGCM(cek).decrypt(nonce, body, None)
    if len(decrypted) < 2:
        raise ValueError("Decrypted aesgcm payload too short for padding length")
    (pad_len,) = struct.unpack(">H", decrypted[:2])
    return decrypted[2 + pad_len :]


def decrypt_webpush_payload(
    data_b64: str,
    keys: WebPushClientKeys,
    headers: dict[str, Any] | None = None,
) -> bytes:
    """Decrypt a Web Push notification payload from Mozilla Autopush."""
    raw = b64url_decode(data_b64)
    norm_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    encoding = norm_headers.get("encoding", norm_headers.get("content-encoding", "aes128gcm")).lower()

    if encoding == "aesgcm":
        return decrypt_aesgcm(
            body=raw,
            keys=keys,
            encryption_header=norm_headers.get("encryption", ""),
            crypto_key_header=norm_headers.get("crypto-key", ""),
        )

    return decrypt_aes128gcm(raw, keys)


def encrypt_aes128gcm(
    plaintext: bytes,
    p256dh: str,
    auth: str,
    record_size: int = 4096,
) -> bytes:
    """Encrypt plaintext using RFC 8291 aes128gcm (used for tests and self-checks)."""
    ua_public_bytes = b64url_decode(p256dh)
    auth_bytes = b64url_decode(auth)

    as_private = ec.generate_private_key(ec.SECP256R1())
    as_public_bytes = as_private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), ua_public_bytes
    )
    shared_secret = as_private.exchange(ec.ECDH(), ua_public)

    salt = os.urandom(16)
    key_info = b"WebPush: info\x00" + ua_public_bytes + as_public_bytes
    ikm = _hkdf_sha256(
        ikm=shared_secret,
        salt=auth_bytes,
        info=key_info,
        length=32,
    )
    cek = _hkdf_sha256(
        ikm=ikm,
        salt=salt,
        info=b"Content-Encoding: aes128gcm\x00",
        length=16,
    )
    nonce = _hkdf_sha256(
        ikm=ikm,
        salt=salt,
        info=b"Content-Encoding: nonce\x00",
        length=12,
    )

    padded = plaintext + b"\x02"
    ciphertext = AESGCM(cek).encrypt(nonce, padded, None)
    header = salt + struct.pack(">I", record_size) + bytes([len(as_public_bytes)]) + as_public_bytes
    return header + ciphertext
