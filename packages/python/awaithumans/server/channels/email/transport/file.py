"""File transport — drops one JSON per email into a directory.

For local development and end-to-end smoke tests. Each `send()` writes
`{timestamp}-{message_id}.json` containing the rendered email so a
test runner can poll the directory, parse the file, extract the magic
link, and POST to the action endpoint without ever touching a real
SMTP server.

Configuration:
  - `dir` (required): absolute path to a writable directory. Created
    on first send if missing.

Use it via the `/api/channels/email/identities` API:

    {
      "transport": "file",
      "transport_config": {"dir": "/tmp/awaithumans-emails"}
    }

Never use this in production: emails are written to disk in plaintext
and never reach a recipient.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from awaithumans.server.channels.email.transport.base import (
    EmailMessage,
    EmailSendResult,
    EmailTransportError,
)

logger = logging.getLogger("awaithumans.server.channels.email.transport.file")


class FileTransport:
    """Write each email as a JSON file under `dir`.

    Filenames are `{unix_ms}-{shortid}.json` so they sort
    chronologically and don't collide across rapid sends. The body
    is the EmailMessage serialized with all fields — `to`, `subject`,
    `html`, `text`, `from_email`, `from_name`, `reply_to`, `tags`.
    Plus a `_received_at` ISO timestamp for tooling that doesn't want
    to parse filenames.
    """

    def __init__(self, *, dir: str) -> None:
        if not dir:
            raise EmailTransportError("file transport: config.dir is required.")
        self._dir = Path(dir).expanduser().resolve()

    @property
    def name(self) -> str:
        return "file"

    async def send(self, message: EmailMessage) -> EmailSendResult:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise EmailTransportError(
                f"file transport: cannot create dir {self._dir}: {exc}"
            ) from exc

        message_id = f"file-{uuid.uuid4().hex[:16]}"
        unix_ms = int(time.time() * 1000)
        filename = self._dir / f"{unix_ms}-{message_id}.json"

        payload = asdict(message)
        payload["_received_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload["_message_id"] = message_id

        try:
            filename.write_text(json.dumps(payload, indent=2))
        except OSError as exc:
            raise EmailTransportError(f"file transport: failed to write {filename}: {exc}") from exc

        logger.info(
            "[email/file] wrote %s (to=%s subject=%s)",
            filename.name,
            message.to,
            message.subject,
        )
        return EmailSendResult(
            message_id=message_id,
            transport=self.name,
        )
