"""Both the suffixed and unsuffixed forms of every code must classify alike."""
import pytest

from app.services.email_errors import (
    ACCOUNT_HALT, PERMANENT, TRANSIENT, TRANSIENT_ACCOUNT, classify_ses_error,
)


@pytest.mark.parametrize("code", ["AccountSendingPaused", "AccountSendingPausedException"])
def test_account_pause_classifies_either_spelling(code):
    """The AWS v1 reference lists wire codes without the Exception suffix that
    boto3's generated classes carry. Matching one form only would leave the
    single protection against a shared-account pause silently dead."""
    assert classify_ses_error(code) == ACCOUNT_HALT


def test_message_rejected_is_permanent():
    assert classify_ses_error("MessageRejected") == PERMANENT


def test_unknown_code_is_transient():
    """Retrying costs a few API calls; calling a real outage permanent loses mail."""
    assert classify_ses_error("SomethingNobodyHasSeen") == TRANSIENT
    assert classify_ses_error(None) == TRANSIENT
