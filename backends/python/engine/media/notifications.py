"""Admin ntfy notifications for the request queue.

Reuses the same self-hosted ntfy instance and the ``homelab`` topic the
homelab alerting (Uptime Kuma on nukbuntu) publishes to, so one phone
subscription covers both. The post is done directly with
``httpx`` (a main dependency) rather than via readable_utils.ntfy_tools —
the docker image builds with ``--no-default-groups``, so nothing from the
drive-sync group (readable-utils, requests, pandas) exists in the container.
Only main-dependency imports are safe anywhere under engine/.
Credentials come from NTFY_URL / NTFY_USERNAME / NTFY_PASSWORD in the
personal.env this repo's .env symlinks to (docker gets them via env_file).

Sending is strictly best-effort: a down or misconfigured ntfy must never
break filing a request, so every failure is swallowed after a log line.
"""

import logging
import os
import threading

import httpx

from ..config import load_env
from .health import format_bytes
from .models import MediaType
from .requests import MediaRequest

logger = logging.getLogger(__name__)

NTFY_TOPIC = "homelab"


def notify_new_request(request: MediaRequest, estimate_bytes: int | None = None) -> None:
    """Tell the admins a request is waiting for approval at /requests.

    ``estimate_bytes`` is the worst-case size the add would take, so the
    admin can tell from the phone ping whether it is a 4 GB movie or a
    200 GB back catalogue before opening the queue.

    Runs in a daemon thread so a slow or unreachable ntfy never stalls
    the UI handler that filed the request.
    """
    threading.Thread(target=_send, args=(request, estimate_bytes), daemon=True).start()


def request_message(request: MediaRequest, estimate_bytes: int | None = None) -> str:
    """'Sync_Plex: jo requested Severance (2022) — show, 3 seasons, ~40 GB — awaiting approval'"""
    result = request.result
    facts = ["show" if result.media_type == MediaType.TV else "movie"]
    if result.season_count:
        facts.append(f"{result.season_count} seasons")
    if estimate_bytes:
        facts.append(f"~{format_bytes(estimate_bytes)}")
    return f"Sync_Plex: {request.requested_by} requested {request.title_line} — {', '.join(facts)} — awaiting approval"


def _send(request: MediaRequest, estimate_bytes: int | None = None) -> None:
    load_env()
    url = os.environ.get("NTFY_URL")
    if not url:
        logger.warning("NTFY_URL not set — skipping notification for request %s", request.id)
        return
    try:
        response = httpx.post(
            f"{url}/{NTFY_TOPIC}",
            auth=(os.environ.get("NTFY_USERNAME", ""), os.environ.get("NTFY_PASSWORD", "")),
            data={"message": request_message(request, estimate_bytes)},
            timeout=10,
        )
        if not response.is_success:
            logger.warning(
                "ntfy rejected the new-request notification for %s: HTTP %s", request.id, response.status_code
            )
    except Exception:
        logger.exception("failed to send ntfy notification for request %s", request.id)
