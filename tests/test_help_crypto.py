import base64

import pytest

from services.help_crypto import HelpDataCipher, HelpDataCryptoError


def key(value):
    return base64.b64encode(bytes([value]) * 32).decode()


def test_ciphertext_is_versioned_randomized_and_authenticated():
    cipher = HelpDataCipher(
        encryption_keys={"v1": key(1)},
        active_version="v1",
        identity_hash_key=key(2),
    )

    first = cipher.encrypt("V-12.345.678", field="beneficiary.identity")
    second = cipher.encrypt("V-12.345.678", field="beneficiary.identity")

    assert first.startswith("enc:v1:")
    assert first != second
    assert "12.345.678" not in first
    assert cipher.decrypt(first, field="beneficiary.identity") == "V-12.345.678"
    with pytest.raises(HelpDataCryptoError):
        cipher.decrypt(first, field="beneficiary.name")


def test_cipher_can_read_old_version_after_rotation():
    old = HelpDataCipher(
        encryption_keys={"v1": key(1)},
        active_version="v1",
        identity_hash_key=key(3),
    )
    ciphertext = old.encrypt("Ana", field="beneficiary.name")
    rotated = HelpDataCipher(
        encryption_keys={"v1": key(1), "v2": key(2)},
        active_version="v2",
        identity_hash_key=key(3),
    )

    assert rotated.decrypt(ciphertext, field="beneficiary.name") == "Ana"
    assert rotated.encrypt("Ana", field="beneficiary.name").startswith("enc:v2:")


def test_identity_hmac_normalizes_equivalent_values_without_exposing_identity():
    cipher = HelpDataCipher(
        encryption_keys={"v1": key(1)},
        active_version="v1",
        identity_hash_key=key(2),
    )

    first = cipher.identity_hash("v-12.345.678")
    second = cipher.identity_hash("V12345678")

    assert first == second
    assert len(first) == 64
    assert "12345678" not in first


def test_cipher_rejects_missing_invalid_or_unknown_keys():
    with pytest.raises(HelpDataCryptoError):
        HelpDataCipher(encryption_keys={}, active_version="v1", identity_hash_key=key(2))
    with pytest.raises(HelpDataCryptoError):
        HelpDataCipher(encryption_keys={"v1": key(1)}, active_version="v2", identity_hash_key=key(2))
    with pytest.raises(HelpDataCryptoError):
        HelpDataCipher(encryption_keys={"v1": "invalid"}, active_version="v1", identity_hash_key=key(2))


def test_binary_evidence_is_randomized_and_authenticated():
    cipher = HelpDataCipher(
        encryption_keys={"v1": key(1)},
        active_version="v1",
        identity_hash_key=key(2),
    )
    payload = b"%PDF-1.4 private evidence marker"

    encrypted = cipher.encrypt_bytes(payload, field="consent.evidence")

    assert encrypted.startswith(b"enc:v1:")
    assert payload not in encrypted
    assert cipher.decrypt_bytes(encrypted, field="consent.evidence") == payload


def test_blind_indexes_are_separated_by_purpose():
    cipher = HelpDataCipher(
        encryption_keys={"v1": key(1)},
        active_version="v1",
        identity_hash_key=key(2),
    )

    email_index = cipher.blind_index("User@Example.com", purpose="responsible-email")
    another_email_index = cipher.blind_index(" user@example.com ", purpose="responsible-email")
    identity_index = cipher.blind_index("user@example.com", purpose="identity")

    assert email_index == another_email_index
    assert email_index != identity_index
    assert len(email_index) == 64
