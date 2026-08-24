"""Classify botocore SES error codes into row actions."""

TRANSIENT = "TRANSIENT"
TRANSIENT_ACCOUNT = "TRANSIENT_ACCOUNT"
ACCOUNT_HALT = "ACCOUNT_HALT"
PERMANENT = "PERMANENT"

_ACCOUNT_HALT = {"AccountSendingPaused", "ConfigurationSetSendingPaused"}
_TRANSIENT_ACCOUNT = {"Throttling", "TooManyRequests"}
_PERMANENT = {"MessageRejected", "MailFromDomainNotVerified", "ConfigurationSetDoesNotExist"}


def classify_ses_error(error_code: str | None) -> str:
    """Normalise the code before matching.

    The AWS v1 API reference lists wire codes WITHOUT the `Exception` suffix
    that boto3's generated exception classes carry. Stripping it is correct
    whichever form botocore emits, and it survives a move to the v2 API where
    the names differ again.
    """
    if not error_code:
        return TRANSIENT
    code = error_code
    if code.endswith("Exception"):
        code = code[: -len("Exception")]
    if code in _ACCOUNT_HALT:
        return ACCOUNT_HALT
    if code in _TRANSIENT_ACCOUNT:
        return TRANSIENT_ACCOUNT
    if code in _PERMANENT:
        return PERMANENT
    return TRANSIENT


class AccountHaltError(Exception):
    """SES has paused sending for the whole account.

    Raised out of per-row processing to end the run. Every later row in the
    batch would hit the same pause, so retrying them burns attempts on a
    condition no row can fix.
    """


class ThrottleStopError(Exception):
    """SES rate-limited the account.

    Raised out of per-row processing to end the run. The row is already
    requeued with backoff; the raise only stops the drainer from hammering a
    limit it has just been told about.
    """
