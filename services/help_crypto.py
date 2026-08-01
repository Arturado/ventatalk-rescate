import base64
import hashlib
import hmac
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from services.help_identity import HelpIdentityError, normalize_venezuelan_identity


class HelpDataCryptoError(ValueError):
    pass


def _decode_key(value, name):
    try:
        decoded = base64.b64decode(str(value or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise HelpDataCryptoError(f"{name} no es base64 valido") from exc
    if len(decoded) != 32:
        raise HelpDataCryptoError(f"{name} debe contener 32 bytes")
    return decoded


class HelpDataCipher:
    def __init__(self, *, encryption_keys, active_version, identity_hash_key):
        self.active_version = str(active_version or "").strip().lower()
        self.encryption_keys = {
            str(version).strip().lower(): _decode_key(value, f"encryption_key_{version}")
            for version, value in encryption_keys.items()
        }
        if not self.encryption_keys or self.active_version not in self.encryption_keys:
            raise HelpDataCryptoError("La version activa de cifrado no esta configurada")
        self.identity_hash_key = _decode_key(identity_hash_key, "identity_hash_key")

    @classmethod
    def from_environment(cls):
        prefix = "HELP_DATA_ENCRYPTION_KEY_"
        keys = {
            name[len(prefix):].lower(): value
            for name, value in os.environ.items()
            if name.startswith(prefix) and value
        }
        return cls(
            encryption_keys=keys,
            active_version=os.getenv("HELP_DATA_ACTIVE_KEY_VERSION"),
            identity_hash_key=os.getenv("HELP_IDENTITY_HASH_KEY"),
        )

    def encrypt(self, plaintext, *, field):
        value = str(plaintext or "")
        if not value:
            raise HelpDataCryptoError("No se puede cifrar un valor vacio")
        nonce = secrets.token_bytes(12)
        payload = AESGCM(self.encryption_keys[self.active_version]).encrypt(
            nonce,
            value.encode(),
            str(field).encode(),
        )
        encoded = base64.urlsafe_b64encode(nonce + payload).decode()
        return f"enc:{self.active_version}:{encoded}"

    def encrypt_bytes(self, plaintext, *, field):
        if not plaintext:
            raise HelpDataCryptoError("No se puede cifrar un archivo vacio")
        nonce = secrets.token_bytes(12)
        payload = AESGCM(self.encryption_keys[self.active_version]).encrypt(
            nonce,
            bytes(plaintext),
            str(field).encode(),
        )
        return f"enc:{self.active_version}:".encode() + base64.urlsafe_b64encode(nonce + payload)

    def decrypt(self, ciphertext, *, field):
        try:
            prefix, version, encoded = str(ciphertext).split(":", 2)
            if prefix != "enc" or version not in self.encryption_keys:
                raise HelpDataCryptoError("Version de cifrado desconocida")
            payload = base64.urlsafe_b64decode(encoded.encode())
            return AESGCM(self.encryption_keys[version]).decrypt(
                payload[:12],
                payload[12:],
                str(field).encode(),
            ).decode()
        except HelpDataCryptoError:
            raise
        except (ValueError, InvalidTag, UnicodeDecodeError) as exc:
            raise HelpDataCryptoError("No fue posible autenticar el dato cifrado") from exc

    def decrypt_bytes(self, ciphertext, *, field):
        try:
            prefix, version, encoded = bytes(ciphertext).split(b":", 2)
            version_name = version.decode()
            if prefix != b"enc" or version_name not in self.encryption_keys:
                raise HelpDataCryptoError("Version de cifrado desconocida")
            payload = base64.urlsafe_b64decode(encoded)
            return AESGCM(self.encryption_keys[version_name]).decrypt(
                payload[:12],
                payload[12:],
                str(field).encode(),
            )
        except HelpDataCryptoError:
            raise
        except (ValueError, InvalidTag, UnicodeDecodeError) as exc:
            raise HelpDataCryptoError("No fue posible autenticar el archivo cifrado") from exc

    def identity_hash(self, identity):
        try:
            normalized = normalize_venezuelan_identity(identity)
        except HelpIdentityError as exc:
            raise HelpDataCryptoError(str(exc)) from exc
        return hmac.new(self.identity_hash_key, normalized.encode(), hashlib.sha256).hexdigest()

    def blind_index(self, value, *, purpose):
        normalized = str(value or "").strip().lower()
        if not normalized or not str(purpose or "").strip():
            raise HelpDataCryptoError("El indice ciego requiere valor y proposito")
        message = f"{purpose}\0{normalized}".encode()
        return hmac.new(self.identity_hash_key, message, hashlib.sha256).hexdigest()
