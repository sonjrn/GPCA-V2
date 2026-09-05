"""Every string a client can read.

Separate from errors.py because these are not errors -- some are successes,
and the ones that are failures are worded to reveal as little as the failing
ones do. What they share is being *visible*, which is the property worth
auditing in one place.

That audit is the reason they are not private constants beside the routes
that use them. Several endpoints must return **byte-identical** text -- that
is the whole enumeration defence -- and a rule like that only holds if it can
be seen at a glance. Scattered across route modules, the next endpoint that
needs the same wording gets a near-copy instead, and nothing fails.

Grouped by what they must not reveal rather than by endpoint, for the same
reason. Two endpoints sharing a constant here is the point, not duplication
to be tidied away.
"""

# Deliberately uninformative, and identical whether or not the address exists.
# A distinct response for "already registered" would turn registration into a
# membership oracle for the whole club roster.
ACCEPTED = "If that address can be registered, we have sent a message to it."

# One message for every login failure. Wrong password, unknown address and
# suspended account are indistinguishable to the caller.
LOGIN_FAILED = "Those credentials are not valid."

# Says the link did not work, and nothing about whether it ever existed,
# expired, or belonged to someone else.
VERIFY_FAILED = "That verification link is not valid. Request a new one."

VERIFIED = "Your email address is verified."

# Unknown and replayed refresh tokens return this same text, so presenting a
# stolen token reveals nothing about whether it was ever real.
REFRESH_FAILED = "That refresh token is not valid. Sign in again."

# Returned whether or not the address has an account, for the same reason
# ACCEPTED is.
RESET_REQUESTED = "If that address has an account, we have sent a reset link to it."

RESET_DONE = "Your password has been changed. Sign in again."

# Expired, already used, wrong purpose and never-existed all read the same.
RESET_FAILED = "That reset link is not valid or has expired. Request a new one."

# Specific on purpose, unlike the others here: the caller is already
# authenticated as this account, so there is nothing left to reveal.
WRONG_PASSWORD = "That is not your current password."
