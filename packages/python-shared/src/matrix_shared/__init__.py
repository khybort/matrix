from matrix_shared.config import Settings, get_settings
from matrix_shared.db import (
    get_engine,
    get_local_engine,
    get_local_session,
    get_session,
    get_shared_engine,
    get_shared_session,
    local_session_scope,
    session_scope,
    shared_session_scope,
)
from matrix_shared.llm import call_claude, call_claude_json, llm_enabled
from matrix_shared.subscription_llm import (
    call_subscription,
    call_subscription_json,
    subscription_enabled,
)
from matrix_shared.trading_safety import (
    LIVE_EXECUTION_REQUIRES_CERT,
    EligibilityVerdict,
    evaluate_eligibility,
    has_valid_certificate,
    maybe_grant_certificate,
)

__all__ = [
    "Settings",
    "get_settings",
    "get_engine",
    "get_local_engine",
    "get_local_session",
    "get_session",
    "get_shared_engine",
    "get_shared_session",
    "local_session_scope",
    "session_scope",
    "shared_session_scope",
    "call_claude",
    "call_claude_json",
    "llm_enabled",
    "call_subscription",
    "call_subscription_json",
    "subscription_enabled",
    "LIVE_EXECUTION_REQUIRES_CERT",
    "EligibilityVerdict",
    "evaluate_eligibility",
    "has_valid_certificate",
    "maybe_grant_certificate",
]
