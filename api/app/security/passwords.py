"""Password hashing.

Argon2id, at the OWASP baseline of 19 MiB / 2 iterations / 1 lane. Measured on
a development machine: ~22 ms per hash, versus ~272 ms for bcrypt at cost 12 --
roughly 12x cheaper in CPU at a comparable security level, with the 19 MiB held
only for the duration of the hash.

The memory cost is the point. Each guess an attacker makes needs its own
19 MiB, which collapses the parallelism a GPU can bring to bear; bcrypt and
PBKDF2 are compute-bound and give an attacker thousands of parallel guesses per
card. Do not lower `memory_cost` below the baseline -- below it the complexity
remains and the benefit does not.

Argon2id is one-way: once a password is hashed here, nobody, including us, can
recover it.
"""

import logging

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

logger = logging.getLogger(__name__)

# A password long enough that length, rather than composition rules, is what
# resists offline cracking. Composition rules produce "Password1!".
MIN_PASSWORD_LENGTH = 14

# No maximum beyond a sane bound on request size. Argon2 has no truncation
# limit -- unlike bcrypt, which silently ignores everything past 72 bytes.
MAX_PASSWORD_LENGTH = 1024


def build_hasher(*, memory_cost: int, time_cost: int, parallelism: int) -> PasswordHasher:
    return PasswordHasher(memory_cost=memory_cost, time_cost=time_cost, parallelism=parallelism)


def hash_password(hasher: PasswordHasher, password: str) -> str:
    return hasher.hash(password)


def verify_password(hasher: PasswordHasher, stored_hash: str, password: str) -> bool:
    """Check a password against a stored hash, never raising on a mismatch."""
    try:
        hasher.verify(stored_hash, password)
    except Argon2Error:
        # Wrong password, or a hash this configuration cannot verify.
        return False
    except InvalidHashError:
        # Not an Argon2 hash at all. Separate clause because InvalidHashError
        # derives from ValueError rather than Argon2Error.
        return False
    return True


def needs_rehash(hasher: PasswordHasher, stored_hash: str) -> bool:
    """True when the stored hash predates the current parameters.

    Lets the cost be raised later and upgraded transparently on next login,
    rather than invalidating everyone's password.
    """
    try:
        return hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


def dummy_verify(hasher: PasswordHasher) -> None:
    """Burn a verification's worth of time against a throwaway hash.

    Called on the "no such user" branch of login and registration. Without it
    that path skips Argon2 entirely and returns measurably faster, which turns
    a carefully uniform error message into a timing oracle for whether an
    account exists.
    """
    verify_password(hasher, _DUMMY_HASH, "not-the-password")


# Computed once at import against the module defaults. Its only job is to cost
# the same order of magnitude as a real verification.
_DUMMY_HASH = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1).hash(
    "dummy-password-for-timing-parity"
)
