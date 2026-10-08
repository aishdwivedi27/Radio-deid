"""Password hashing and policy (SPEC §7). TR-SEC-01.

argon2id (argon2-cffi defaults). Policy (D-026): 12-128 characters with a capital letter, a small letter,
a number and a special character, and not containing the username. Messages never echo the password.
"""

from __future__ import annotations

import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 128  # bounds the hashing cost of one request

_HASHER = PasswordHasher()  # argon2id
_DUMMY_HASH = _HASHER.hash("timing-equaliser-not-a-password")
_SPECIALS = set(string.punctuation)


class PasswordPolicyError(ValueError):
    """The password does not meet the policy. The message never contains the password."""


def check_policy(password: str, username: str = "") -> None:
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise PasswordPolicyError(f"password must be {MIN_LENGTH}-{MAX_LENGTH} characters")
    missing = [
        name
        for name, ok in (
            ("a capital letter", any(c.isupper() for c in password)),
            ("a small letter", any(c.islower() for c in password)),
            ("a number", any(c.isdigit() for c in password)),
            ("a special character", any(c in _SPECIALS for c in password)),
        )
        if not ok
    ]
    if missing:
        raise PasswordPolicyError("password needs " + ", ".join(missing))
    if username and len(username) >= 3 and username.lower() in password.lower():
        raise PasswordPolicyError("password must not contain the username")


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Constant-work check: with no stored hash a dummy hash is verified, so timing does not reveal
    whether the username exists."""
    if len(password) > MAX_LENGTH:
        return False
    try:
        return _HASHER.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _HASHER.check_needs_rehash(stored_hash)


def generate_temporary() -> str:
    """A 16-character temporary password that meets the policy (shown once to the admin)."""
    alphabet = string.ascii_letters + string.digits + "!#%+-=?@"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        try:
            check_policy(pw)
        except PasswordPolicyError:
            continue
        return pw
