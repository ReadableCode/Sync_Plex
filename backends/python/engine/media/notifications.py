"""Admin ntfy notifications for the request queue.

Reuses the same self-hosted ntfy instance and ``house_power`` topic the
other home automation already publishes to (readable_utils.ntfy_tools).
Credentials come from NTFY_URL / NTFY_USERNAME / NTFY_PASSWORD in the
personal.env this repo's .env symlinks to (docker gets them via env_file).

Sending is strictly best-effort: a down or misconfigured ntfy must never
break filing a request, so every failure is swallowed after a log line.
"""

import logging
import threading

from ..config import load_env
from .requests import MediaRequest

logger = logging.getLogger(__name__)

NTFY_TOPIC = "house_power"


def notify_new_request(request: MediaRequest) -> None:
    """Tell the admins a request is waiting for approval at /requests.

    Runs in a daemon thread — send_notification has no timeout, and an
    unreachable ntfy must not stall the UI handler that filed the request.
    """
    threading.Thread(target=_send, args=(request,), daemon=True).start()


def _send(request: MediaRequest) -> None:
    load_env()  # ntfy_tools reads NTFY_* from the environment at import time
    try:
        from readable_utils.ntfy_tools import send_notification

        sent = send_notification(
            NTFY_TOPIC,
            f"Sync_Plex: {request.requested_by} requested {request.title_line} — awaiting approval",
        )
        if not sent:
            logger.warning("ntfy rejected the new-request notification for %s", request.id)
    except Exception:
        logger.exception("failed to send ntfy notification for request %s", request.id)
