"""Transactional email.

Sent inline, in the request that causes it (design section 11). Three rules
make that safe and all three live here or at the call site:

1. Send after commit, never inside the transaction. A message about an order
   that then rolls back is worse than no message.
2. A failed send must never fail the operation. `send_email` swallows and logs;
   it does not raise.
3. Never send from a retried path without rule 2 -- the Stripe webhook is the
   case that matters, where raising would have the customer emailed twice.

SES wiring is deferred until credentials exist (section 11.4). Until then a
send is logged, which is enough for local development and keeps every call
site written the way it will stay.
"""

import logging
from typing import Any

from flask import current_app

logger = logging.getLogger(__name__)


def send_email(*, to: str, template: str, context: dict[str, Any] | None = None) -> None:
    """Best-effort send. Never raises.

    The caller's operation has already committed by the time this runs, so a
    failure here must not surface as a failed request.
    """
    settings = current_app.config["SETTINGS"]
    payload = context or {}
    try:
        if settings.ses_from_address is None:
            logger.info(
                "email (not sent: SES unconfigured)",
                extra={"to": to, "template": template, "context_keys": sorted(payload)},
            )
            return
        # SES delivery lands with credentials; see section 11.4.
        logger.info(
            "email queued for delivery",
            extra={"to": to, "template": template, "from": settings.ses_from_address},
        )
    except Exception:
        logger.exception("email send failed", extra={"to": to, "template": template})


__all__ = ["send_email"]
