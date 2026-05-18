"""Email task notification — the public entry point from task creation.

Parses the task's `notify` list for email routes, resolves the sender
identity (a DB row, or the env-configured default), picks the transport,
renders the email, and sends. Same BackgroundTask pattern as Slack:

- Acquire a fresh DB session (caller's is already released).
- Parse `notify` via the shared routing parser.
- For each route, resolve identity → transport → message → send.
- All errors logged, nothing raised — a send failure never rolls back
  the task the user just created.
"""

from __future__ import annotations

import logging
from typing import Any

from awaithumans.forms import FormDefinition
from awaithumans.server.channels.email.renderer import build_notification_email
from awaithumans.server.channels.email.transport import (
    EmailTransport,
    EmailTransportError,
    resolve_transport,
)
from awaithumans.server.channels.email.transport.factory import (
    resolve_default_transport,
    resolve_identity_transport,
)
from awaithumans.server.channels.routing import ChannelRoute, routes_for_channel
from awaithumans.server.core.config import settings
from awaithumans.server.db.connection import get_async_session_factory
from awaithumans.server.db.models import EmailSenderIdentity
from awaithumans.server.services.email_identity_service import (
    get_identity,
    list_identities,
)
from awaithumans.server.services.notification_audit import (
    record_notification_failure,
)
from awaithumans.utils.time import to_utc_unix

logger = logging.getLogger("awaithumans.server.channels.email.notifier")


async def notify_task(
    *,
    task_id: str,
    task_title: str,
    task_payload: dict[str, Any] | None,
    redact_payload: bool,
    notify: list[str] | None,
    form_definition: dict[str, Any] | None,
) -> None:
    """Send the notification email to each email: route on the task."""
    routes = routes_for_channel(notify, "email")
    if not routes:
        return

    factory = get_async_session_factory()
    form = _parse_form(form_definition)

    async with factory() as session:
        # Pull the task once so we can sign the dashboard handoff URL
        # with the correct expiry. If the task was deleted between the
        # route handler and this background run there's nothing to
        # notify about — bail.
        from awaithumans.server.services.task_service import get_task

        try:
            task = await get_task(session, task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify_task (email): task %s missing: %s", task_id, exc)
            return

        # `task.timeout_at` comes back from SQLite naive even though we
        # wrote it tz-aware. `to_utc_unix` coerces; calling `.timestamp()`
        # directly here would silently shift the URL's expiry by the
        # local-UTC offset and kill links at birth for east-of-UTC users.
        handoff_exp_unix = to_utc_unix(task.timeout_at) if task.timeout_at else None

        task_status = task.status.value if hasattr(task.status, "value") else str(task.status)

        for route in routes:
            try:
                await _deliver_one(
                    session,
                    route,
                    task_id=task_id,
                    task_status=task_status,
                    task_title=task_title,
                    task_payload=task_payload,
                    redact_payload=redact_payload,
                    form=form,
                    handoff_exp_unix=handoff_exp_unix,
                )
            except EmailTransportError as exc:
                logger.error(
                    "Email send failed for task %s → %s: %s",
                    task_id,
                    route.target,
                    exc,
                )
                await record_notification_failure(
                    session,
                    task_id=task_id,
                    task_status=task_status,
                    channel="email",
                    recipient=route.target,
                    reason="transport_error",
                    message=f"Email transport returned an error: {exc}",
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Unexpected email notifier error for task %s → %s: %s",
                    task_id,
                    route.target,
                    exc,
                )
                await record_notification_failure(
                    session,
                    task_id=task_id,
                    task_status=task_status,
                    channel="email",
                    recipient=route.target,
                    reason="internal_error",
                    message=f"Unexpected error sending email: {exc}",
                )


async def _deliver_one(
    session: Any,
    route: ChannelRoute,
    *,
    task_id: str,
    task_status: str,
    task_title: str,
    task_payload: dict[str, Any] | None,
    redact_payload: bool,
    form: FormDefinition | None,
    handoff_exp_unix: int | None = None,
) -> None:
    identity = await _resolve_identity(session, route.identity)
    transport = await _resolve_transport_for(identity)
    if transport is None:
        logger.warning(
            "Email route %s → no usable transport (identity=%s, default=%s); skipping.",
            route.target,
            route.identity,
            settings.EMAIL_TRANSPORT,
        )
        await record_notification_failure(
            session,
            task_id=task_id,
            task_status=task_status,
            channel="email",
            recipient=route.target,
            reason="no_transport_configured",
            message=(
                "No email transport is configured. Set "
                "AWAITHUMANS_EMAIL_TRANSPORT (and the matching credentials) "
                "or create an email sender identity in Settings."
            ),
        )
        return

    from_email, from_name, reply_to = _resolve_from(identity)
    if not from_email:
        logger.warning(
            "Email route %s → no From: address configured (set EMAIL_FROM "
            "or create an identity); skipping.",
            route.target,
        )
        await record_notification_failure(
            session,
            task_id=task_id,
            task_status=task_status,
            channel="email",
            recipient=route.target,
            reason="no_from_address",
            message=(
                "Email transport is configured but no From: address is set. "
                "Set AWAITHUMANS_EMAIL_FROM or configure a sender identity "
                "with a From: address."
            ),
        )
        return

    message = build_notification_email(
        to=route.target,
        task_id=task_id,
        task_title=task_title,
        task_payload=task_payload,
        redact_payload=redact_payload,
        form=form,
        from_email=from_email,
        from_name=from_name,
        reply_to=reply_to,
        public_url=settings.PUBLIC_URL,
        handoff_exp_unix=handoff_exp_unix,
    )
    result = await transport.send(message)
    logger.info(
        "Email sent for task %s → %s (transport=%s, id=%s)",
        task_id,
        route.target,
        result.transport,
        result.message_id,
    )


async def _resolve_identity(session: Any, identity_id: str | None) -> EmailSenderIdentity | None:
    """Resolve which sender identity a route should use.

    Precedence:
      1. Explicit `email+<id>:...` → that identity (None + log if missing).
      2. Bare `email:...` AND env transport (`AWAITHUMANS_EMAIL_TRANSPORT`)
         is set → None (caller uses env-derived transport, unchanged).
      3. Bare `email:...` AND env transport is unset AND exactly one
         identity is configured in the DB → use that one.
      4. Bare `email:...` AND env transport is unset AND multiple
         identities exist → None + log a clear "be explicit" warning.

    Path 3 is the UX fix for dashboard-configured users: the docs'
    quickstart `notify=["email:reviewer@acme.com"]` example would
    otherwise silently skip when the operator set up email through
    the dashboard instead of env vars. Solo identity = unambiguous
    default; >1 identity = we don't pick arbitrarily.
    """
    if identity_id is not None:
        row = await get_identity(session, identity_id)
        if row is None:
            logger.warning(
                "Email identity '%s' not found; falling back to env default.",
                identity_id,
            )
        return row

    # Bare `email:` route. If env vars provide a default transport,
    # honor that (existing deployments don't change behavior).
    if settings.EMAIL_TRANSPORT:
        return None

    rows = await list_identities(session)
    if len(rows) == 1:
        # list_identities defers transport_config (PR #100) so we
        # re-fetch via get_identity to load the encrypted column.
        full = await get_identity(session, rows[0].id)
        if full is not None:
            logger.info(
                "Bare 'email:' route resolved to single configured identity '%s'.",
                full.id,
            )
        return full
    if len(rows) > 1:
        logger.warning(
            "Bare 'email:' route but %d identities configured and no "
            "AWAITHUMANS_EMAIL_TRANSPORT set — route via 'email+<id>:...' "
            "or set env vars. Skipping send.",
            len(rows),
        )
    return None


async def _resolve_transport_for(
    identity: EmailSenderIdentity | None,
) -> EmailTransport | None:
    if identity is not None:
        return resolve_identity_transport(identity)
    return resolve_default_transport()


def _resolve_from(
    identity: EmailSenderIdentity | None,
) -> tuple[str | None, str | None, str | None]:
    """Return (from_email, from_name, reply_to)."""
    if identity is not None:
        return identity.from_email, identity.from_name, identity.reply_to
    return (
        settings.EMAIL_FROM,
        settings.EMAIL_FROM_NAME,
        settings.EMAIL_REPLY_TO,
    )


def _parse_form(form_definition: dict[str, Any] | None) -> FormDefinition | None:
    if not form_definition:
        return None
    try:
        return FormDefinition.model_validate(form_definition)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Invalid form_definition on task: %s", exc)
        return None


# Re-exported for tests and callers that want the low-level transport directly.
__all__ = ["notify_task", "resolve_transport"]
