"""
Secure credential storage.

Primary backend: system keyring (via the `keyring` library — optional dep).
Fallback: AES-256-GCM encrypted file in the app config directory, with a key
derived from the machine ID via PBKDF2-HMAC-SHA256.

Usage
-----
    from gui.secret_store import SecretStore
    store = SecretStore()

    # Store an API key
    store.set("openai-api-key", "sk-...")

    # Retrieve it
    key = store.get("openai-api-key")          # returns None if not found

    # Generate and persist a random 32-byte encryption key
    key_bytes = store.get_or_create_bytes("cache-encryption-key")
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SERVICE = "bethesda-strings-editor"
_PBKDF2_ITERATIONS = 480_000   # NIST SP 800-132 recommended minimum
_PBKDF2_SALT = b"bse-secret-store-v1-machine-salt"


# ── Machine-ID helper ─────────────────────────────────────────────────


def _machine_id() -> str:
    """Return a stable, machine-unique string.  Not a secret itself, but
    combined with PBKDF2 it produces a per-machine key."""
    # Linux
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            text = Path(path).read_text().strip()
            if text:
                return text
        except OSError:
            pass
    # Windows
    if platform.system() == "Windows":
        try:
            import winreg  # type: ignore[import]
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            )
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(guid)
        except Exception:
            pass
    # Fallback: hash of network node + platform
    node = str(platform.node())
    return hashlib.sha256(node.encode()).hexdigest()


def _derive_machine_key() -> bytes:
    """Derive a 256-bit key from the machine ID.  Reproducible on the same
    machine without storing anything; provides opportunistic protection against
    file exfiltration."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_PBKDF2_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(_machine_id().encode())


# ── Encryption helpers ────────────────────────────────────────────────

_MAGIC = b"BSS\x01"   # BethesdaStringsStore v1


def _encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt with AES-256-GCM.  Returns magic + 12-byte nonce + ciphertext+tag."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, _MAGIC)
    return _MAGIC + nonce + ct


def _decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt data produced by _encrypt.  Raises ValueError on tamper."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not data.startswith(_MAGIC):
        raise ValueError("Not a BSS encrypted blob")
    nonce = data[4:16]
    ct = data[16:]
    return AESGCM(key).decrypt(nonce, ct, _MAGIC)


# ── Fallback encrypted-file store ────────────────────────────────────

class _FileStore:
    """Encrypted JSON key-value store for when the system keyring is absent."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._key = _derive_machine_key()
        self._data: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_bytes()
            plain = _decrypt(raw, self._key)
            return json.loads(plain.decode())
        except Exception as e:
            logger.warning("Could not load encrypted secret store: %s", e)
            return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            blob = _encrypt(json.dumps(self._data).encode(), self._key)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_bytes(blob)
            tmp.replace(self._path)
        except Exception as e:
            logger.error("Could not save encrypted secret store: %s", e)

    def get(self, name: str) -> Optional[str]:
        return self._data.get(name)

    def set(self, name: str, value: str) -> None:
        self._data[name] = value
        self._save()

    def delete(self, name: str) -> None:
        self._data.pop(name, None)
        self._save()


# ── Public API ────────────────────────────────────────────────────────

class SecretStore:
    """
    Credential store backed by the system keyring (if available) or an
    AES-256-GCM encrypted file derived from the machine ID.

    The machine-derived key means the file store is not portable — copying the
    file to another machine produces unreadable ciphertext.  This is intentional:
    it provides opportunistic protection against credential exfiltration via file
    copy without requiring a user password.
    """

    def __init__(self, fallback_path: Optional[Path] = None) -> None:
        self._fallback_path = fallback_path
        self._keyring_available: Optional[bool] = None   # lazy probe
        self._file_store: Optional[_FileStore] = None

    def _keyring(self):  # type: ignore[return]
        """Return the keyring module or None if unavailable."""
        if self._keyring_available is False:
            return None
        try:
            import keyring  # type: ignore[import]
            # Quick probe — some backends raise on first use
            keyring.get_password(_SERVICE, "__probe__")
            self._keyring_available = True
            return keyring
        except Exception as e:
            logger.info("System keyring unavailable (%s); using encrypted file store", e)
            self._keyring_available = False
            return None

    def _file(self) -> _FileStore:
        if self._file_store is None:
            path = self._fallback_path or (
                Path.home() / ".config" / "BethesdaModTools" / "secrets.bss"
            )
            self._file_store = _FileStore(path)
        return self._file_store

    def get(self, name: str) -> Optional[str]:
        """Retrieve a stored secret.  Returns None if not found."""
        kr = self._keyring()
        if kr is not None:
            try:
                return kr.get_password(_SERVICE, name)
            except Exception as e:
                logger.warning("Keyring get failed: %s", e)
        return self._file().get(name)

    def set(self, name: str, value: str) -> None:
        """Store a secret.  Writes to keyring if available, file store otherwise."""
        kr = self._keyring()
        if kr is not None:
            try:
                kr.set_password(_SERVICE, name, value)
                return
            except Exception as e:
                logger.warning("Keyring set failed, falling back to file store: %s", e)
        self._file().set(name, value)

    def delete(self, name: str) -> None:
        """Remove a stored secret."""
        kr = self._keyring()
        if kr is not None:
            try:
                kr.delete_password(_SERVICE, name)
            except Exception:
                pass
        if self._file_store:
            self._file_store.delete(name)

    def get_or_create_bytes(self, name: str, length: int = 32) -> bytes:
        """Return the stored key as raw bytes, creating a new random one if absent."""
        stored = self.get(name)
        if stored:
            try:
                return base64.b64decode(stored)
            except Exception:
                pass
        new_key = os.urandom(length)
        self.set(name, base64.b64encode(new_key).decode())
        logger.info("Generated new %d-byte key for '%s'", length, name)
        return new_key

    def backend_name(self) -> str:
        """Return a human-readable name of the active backend."""
        if self._keyring_available is None:
            self._keyring()   # trigger probe
        if self._keyring_available:
            try:
                import keyring
                return type(keyring.get_keyring()).__name__
            except Exception:
                pass
        return "encrypted file (machine-key)"


# Module-level singleton — import and use directly in most cases.
_store: Optional[SecretStore] = None


def get_store() -> SecretStore:
    global _store
    if _store is None:
        _store = SecretStore()
    return _store
