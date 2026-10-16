"""Separate scored Agent outcomes from broken execution, without reading prose.

Only typed framework failures and the adapter's structured native-failure record
affect classification. Tool output, prompts, and exception messages are not
searched for HTTP status codes. Raw rewards and native logs remain unchanged.
"""
from collections.abc import Mapping


PROVIDER_OR_SETUP_FAILURES = frozenset({
    "AgentAuthenticationError", "ModelNotFoundError",
    "UnsupportedModelParametersError", "ApiError", "ApiRateLimitError",
    "ApiUsageLimitError", "ApiInternalServerError", "ApiOverloadedError",
    "ApiConnectionClosedError", "ApiResponseStalledError", "UnknownApiError",
    "ApiProviderResourceNotFoundError", "NetworkConnectionError",
    "ApiConnectionError", "ProviderUnavailableError", "SkillsAgentProviderError",
    "SkillsAgentLifecycleError", "AgentSetupTimeoutError", "EnvironmentCleanupError",
    "EnvironmentHealthError", "ResourceConfigurationError",
})

# Replacing a trial is allowed only for these identifiable environment faults.
# Provider outages are retried within the same Agent request, never by sampling
# a whole new answer behind the original slot.
REPLACEABLE_INFRA_FAILURES = frozenset({
    "EnvironmentStartTimeoutError", "DockerDaemonConnectionError",
    "EnvironmentConnectionError",
})

# These limits are part of the Agent's measured run. A completed verifier can
# score the state it left behind, including an ordinary unsuccessful zero.
SCORABLE_AGENT_LIMITS = frozenset({
    "AgentTimeoutError", "OutputTokenExceededError", "ContextWindowExceededError",
})


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _assessment(status, reason):
    return {"status": status, "reason": reason,
            "pause_admission": status == "needs_review"}


def assess_outcome(result):
    """Return status plus a safe, bounded reason for the admission controller."""
    if not isinstance(result, Mapping):
        return _assessment("needs_review", "invalid_result")
    native = _mapping(_mapping(result.get("agent_result")).get("metadata")).get("native_failure")
    if native:
        kind = _mapping(native).get("kind")
        kind = kind if isinstance(kind, str) else ""
        # Native output/context limits have the same scoring semantics as the
        # framework limit exceptions below. They must not hide a cleanup error.
        agent_limit = (kind == "agent_limit"
                       and _mapping(native).get("category") == "output_limit"
                       and not _mapping(native).get("cleanup_error_count"))
        if not agent_limit:
            reason = {"provider": "native_provider_failure",
                      "lifecycle": "native_lifecycle_failure",
                      "agent_exit": "native_agent_exit"}.get(kind, "native_failure")
            return _assessment("needs_review", reason)

    errors = [result[key] for key in ("exception_info", "controller_error")
              if result.get(key)]
    names = [_mapping(error).get("exception_type") or _mapping(error).get("type")
             for error in errors]
    names = [name if isinstance(name, str) else None for name in names]
    if any(name in PROVIDER_OR_SETUP_FAILURES for name in names):
        return _assessment("needs_review", "provider_or_setup_failure")
    if any(name in REPLACEABLE_INFRA_FAILURES for name in names):
        if all(name in REPLACEABLE_INFRA_FAILURES for name in names):
            return _assessment("infra_failure", "environment_transport_failure")
        return _assessment("needs_review", "mixed_execution_failure")
    if errors and any(name not in SCORABLE_AGENT_LIMITS for name in names):
        # A generic nonzero exit could conceal a broken gateway or missing ps.
        # Keep its zero visible for review instead of treating it as capability.
        return _assessment("needs_review", "unclassified_execution_failure")

    reward = _mapping(_mapping(result.get("verifier_result")).get("rewards")).get("reward")
    if type(reward) in (int, float) and reward in (0, 1):
        return _assessment("complete", "verified_binary_reward")
    return _assessment("needs_review", "missing_or_invalid_reward")


def classify_result(result):
    return assess_outcome(result)["status"]
