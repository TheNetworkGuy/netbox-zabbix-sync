"""
NetBox and Zabbix API clients that retry requests on transient failures.

Both clients follow the same policy: 3 attempts in total, retry on connection
errors and 502/503/504 responses, and do not resend a non-idempotent request
when the first one may already have been processed by the server.

How the retries are added differs per client:
- NetBox: pynetbox uses a requests Session, so a urllib3 Retry is mounted on it.
- Zabbix: zabbix_utils uses the standard library urllib, which has no retry
  support, so each API request is wrapped with tenacity instead.
"""

import socket
import ssl
from http.client import HTTPException
from urllib.error import HTTPError, URLError

from pynetbox.core.api import Api
from requests.adapters import HTTPAdapter
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from urllib3 import Retry
from zabbix_utils import ProcessingError, ZabbixAPI

from netbox_zabbix_sync.modules.logging import get_logger

# Shared retry policy: 1 try + 2 retries = 3 attempts.
HTTP_RETRIES = 2
HTTP_BACKOFF_FACTOR = 1
HTTP_RETRY_STATUSES = frozenset({502, 503, 504})

logger = get_logger()


# ----------------------------------------------------------------------------
# NetBox
# ----------------------------------------------------------------------------

NETBOX_RETRY = Retry(
    total=HTTP_RETRIES,
    # Waits 0s, then 2s (urllib3 retries the first time immediately).
    backoff_factor=HTTP_BACKOFF_FACTOR,
    status_forcelist=HTTP_RETRY_STATUSES,
    # PATCH is idempotent, so safe to retry. POST is left out to avoid
    # creating duplicate objects when a response gets lost.
    allowed_methods=Retry.DEFAULT_ALLOWED_METHODS | {"PATCH"},
    # Once retries are exhausted, hand the last response back to pynetbox so it
    # raises its usual RequestError instead of requests.exceptions.RetryError.
    raise_on_status=False,
)


class RetryingNetBoxAPI(Api):
    """pynetbox API that retries requests on transient failures.

    The retry adapter is mounted in the constructor, so it is in place before
    the first request is made.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        adapter = HTTPAdapter(max_retries=NETBOX_RETRY)
        self.http_session.mount("https://", adapter)
        self.http_session.mount("http://", adapter)


# ----------------------------------------------------------------------------
# Zabbix
# ----------------------------------------------------------------------------

# Exceptions that can signal a transient failure. Anything else, such as an
# APIRequestError returned by Zabbix itself, is never retried.
TRANSIENT_ERROR_TYPES = (ProcessingError, OSError, HTTPException)

# Errors raised while connecting, before the request reached Zabbix.
# Retrying these is safe for any method.
CONNECT_ERRORS = (ConnectionRefusedError, socket.gaierror, TimeoutError)

# Methods that should not be sent twice. If Zabbix processed the first request
# but the response got lost, a retry would fail with "already exists" (create)
# or "does not exist" (delete), even though the change went through.
NON_IDEMPOTENT_SUFFIXES = (".create", ".delete")


def is_idempotent(method: str) -> bool:
    """Return True if the Zabbix API method can safely be sent more than once."""
    return not method.endswith(NON_IDEMPOTENT_SUFFIXES)


def retry_reason(error: BaseException | None, method: str) -> str | None:
    """Return why a failed Zabbix request may be retried, or None if it may not."""
    if not isinstance(error, TRANSIENT_ERROR_TYPES):
        return None
    idempotent = is_idempotent(method)
    if not isinstance(error, ProcessingError):
        # Raw OSError / HTTPException: zabbix_utils does not wrap errors raised
        # while waiting for or reading the response (read timeout, connection
        # reset). The request was sent, so only idempotent methods are retried.
        return f"{type(error).__name__}: {error}" if idempotent else None

    # zabbix_utils raises ProcessingError "from None", which hides the original
    # exception from tracebacks but keeps it in __context__.
    cause = error.__context__
    if isinstance(cause, HTTPError):
        reason = f"HTTP {cause.code}"
        retryable = cause.code in HTTP_RETRY_STATUSES and idempotent
    elif isinstance(cause, URLError):
        reason = str(cause.reason)
        # A bad certificate will not fix itself.
        retryable = not isinstance(cause.reason, ssl.SSLCertVerificationError) and (
            isinstance(cause.reason, CONNECT_ERRORS) or idempotent
        )
    else:
        # Anything else (e.g. a response that is not valid JSON) is not transient.
        reason, retryable = "", False
    return reason if retryable else None


class RetryingZabbixAPI(ZabbixAPI):
    """ZabbixAPI that retries requests on transient failures.

    Every API call, including the version check and login done in the
    constructor, goes through send_api_request, so overriding it covers them all.
    """

    def send_api_request(
        self, method: str, params: dict | None = None, need_auth=True
    ) -> dict:
        def log_retry(retry_state: RetryCallState) -> None:
            outcome = retry_state.outcome
            error = outcome.exception() if outcome else None
            logger.warning(
                "Zabbix API call %s failed (%s). Retrying in %ss (retry %d of %d).",
                method,
                retry_reason(error, method),
                retry_state.upcoming_sleep,
                retry_state.attempt_number,
                HTTP_RETRIES,
            )

        retryer = Retrying(
            retry=retry_if_exception(lambda e: retry_reason(e, method) is not None),
            stop=stop_after_attempt(HTTP_RETRIES + 1),
            # Waits 1s, then 2s. (urllib3 on the NetBox side waits 0s, then 2s.)
            wait=wait_exponential(multiplier=HTTP_BACKOFF_FACTOR),
            before_sleep=log_retry,
            # Raise the last error itself instead of tenacity's RetryError.
            reraise=True,
        )
        return retryer(super().send_api_request, method, params, need_auth)
