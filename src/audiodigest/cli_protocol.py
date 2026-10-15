"""Fixed CLI failure classification; no provider text or identity leaves this module."""

from typing import Any

CLI_FAILURE_CODES = {
    "cli_empty", "cli_shape", "cli_status", "cli_response_missing", "cli_error",
    "cli_auth", "cli_access", "cli_quota", "cli_rate_limit", "cli_unavailable",
    "cli_request", "cli_action_denied", "cli_incomplete",
}
RECOVERABLE_CLI_CODES = {
    "cli_empty", "cli_shape", "cli_status", "cli_response_missing", "cli_error",
    "cli_rate_limit", "cli_unavailable", "cli_incomplete", "model_exit",
}


def provider_failure(wrapper: Any) -> tuple[str, int | None]:
    """Classify explicit codes only, never infer a provider cause from news/message text."""
    if not isinstance(wrapper, dict):
        return "cli_error", None
    tags: set[str] = set()
    statuses: set[int] = set()
    pending = [(wrapper, 0)]
    while pending:
        node, depth = pending.pop()
        for key in ("code", "status", "type", "httpStatus", "http_status", "statusCode"):
            value = node.get(key)
            if isinstance(value, str):
                # Only exact tags are compared; arbitrary values are never returned.
                tag = value.strip().upper()
                tags.add(tag)
                if (len(tag) == 3 and tag.isascii() and tag.isdigit()
                        and 100 <= int(tag) <= 599):
                    statuses.add(int(tag))
            elif isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
                statuses.add(value)
        if depth < 3:
            for key in ("error", "cause"):
                child = node.get(key)
                if isinstance(child, dict):
                    pending.append((child, depth + 1))
                elif isinstance(child, str):
                    tags.add(child.strip().upper())
    # A nested permanent failure must not be obscured by a generic outer retry code.
    for allowed, numbers, code in (
        ({"BILLING_REQUIRED", "PAYMENT_REQUIRED", "INSUFFICIENT_CREDITS"}, {402}, "billing"),
        ({"UNAUTHENTICATED", "AUTHENTICATION_ERROR", "INVALID_CREDENTIALS",
          "INVALID_GRANT", "TOKEN_EXPIRED"}, {401}, "cli_auth"),
        ({"PERMISSION_DENIED", "FORBIDDEN", "ACCESS_DENIED"}, {403}, "cli_access"),
        ({"QUOTA_EXCEEDED", "QUOTA_EXHAUSTED", "INSUFFICIENT_QUOTA",
          "USAGE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED"}, set(), "cli_quota"),
        ({"INVALID_ARGUMENT", "INVALID_REQUEST", "UNSUPPORTED_MODEL", "MODEL_NOT_FOUND",
          "NOT_FOUND"}, {400, 404, 405, 413, 422}, "cli_request"),
        ({"RATE_LIMITED", "RATE_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS", "RATE_LIMIT"},
         {429}, "cli_rate_limit"),
        ({"UNAVAILABLE", "INTERNAL", "INTERNAL_ERROR", "OVERLOADED", "DEADLINE_EXCEEDED"},
         {408, 425, 500, 502, 503, 504}, "cli_unavailable"),
    ):
        matched = statuses & numbers
        if tags & allowed or matched:
            return code, min(matched or statuses) if statuses else None
    return "cli_error", min(statuses) if statuses else None
