"""Password hashing primitives."""

import time

import pytest

from app.security.passwords import (
    MIN_PASSWORD_LENGTH,
    build_hasher,
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)

PASSWORD = "a-perfectly-reasonable-passphrase"


@pytest.fixture
def hasher():
    return build_hasher(memory_cost=19456, time_cost=2, parallelism=1)


def test_a_password_verifies_against_its_own_hash(hasher) -> None:
    assert verify_password(hasher, hash_password(hasher, PASSWORD), PASSWORD)


def test_a_different_password_does_not_verify(hasher) -> None:
    assert not verify_password(hasher, hash_password(hasher, PASSWORD), "something else")


def test_verification_returns_false_rather_than_raising(hasher) -> None:
    """A malformed stored hash must not 500 the login endpoint."""
    assert verify_password(hasher, "not-a-hash", PASSWORD) is False


def test_the_same_password_hashes_differently_each_time(hasher) -> None:
    """Per-hash salt: identical passwords must not produce identical rows."""
    assert hash_password(hasher, PASSWORD) != hash_password(hasher, PASSWORD)


def test_the_plaintext_never_appears_in_the_hash(hasher) -> None:
    assert PASSWORD not in hash_password(hasher, PASSWORD)


def test_a_long_passphrase_is_not_truncated(hasher) -> None:
    """The bcrypt failure mode this choice avoids.

    bcrypt silently ignores everything past 72 bytes, so two different long
    passphrases sharing a prefix would both verify. Argon2 has no such limit.
    """
    base = "x" * 80
    stored = hash_password(hasher, base + "-ending-one")
    assert verify_password(hasher, stored, base + "-ending-one")
    assert not verify_password(hasher, stored, base + "-ending-two")


def test_needs_rehash_reports_raised_parameters(hasher) -> None:
    """Lets the cost rise later without invalidating anyone's password."""
    stored = hash_password(hasher, PASSWORD)
    assert needs_rehash(hasher, stored) is False

    stronger = build_hasher(memory_cost=19456, time_cost=4, parallelism=1)
    assert needs_rehash(stronger, stored) is True
    # The old hash still verifies, so the upgrade can happen on next login.
    assert verify_password(stronger, stored, PASSWORD)


def test_needs_rehash_tolerates_a_malformed_hash(hasher) -> None:
    assert needs_rehash(hasher, "garbage") is False


def test_dummy_verify_costs_comparable_time(hasher) -> None:
    """The no-such-user branch must not be measurably faster.

    Without this the uniform "invalid credentials" message is undone by a
    stopwatch: a missing account skips Argon2 and returns far sooner.
    """
    stored = hash_password(hasher, PASSWORD)

    start = time.perf_counter()
    verify_password(hasher, stored, PASSWORD)
    real = time.perf_counter() - start

    start = time.perf_counter()
    dummy_verify(hasher)
    dummy = time.perf_counter() - start

    # Same order of magnitude is the property that matters; exact parity is
    # neither achievable nor necessary.
    assert dummy > real / 4, f"dummy {dummy:.4f}s vs real {real:.4f}s"


def test_minimum_length_is_a_length_rule_not_a_composition_rule() -> None:
    assert MIN_PASSWORD_LENGTH >= 12
