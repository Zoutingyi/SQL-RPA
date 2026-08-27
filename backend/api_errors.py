"""Stable API error contract shared by exception handlers and middleware."""

ERROR_CODES = {
    400: "INVALID_REQUEST", 401: "AUTH_REQUIRED", 403: "PERMISSION_DENIED",
    404: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION_ERROR",
    429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 503: "SERVICE_UNAVAILABLE",
}


def error_body(status: int, message, request_id: str = "", code: str | None = None) -> dict:
    text = message if isinstance(message, str) else "Request validation failed"
    stable_code = code or ERROR_CODES.get(status, "API_ERROR")
    field_errors = {}
    if isinstance(message, list):
        for item in message:
            location = item.get("loc", []) if isinstance(item, dict) else []
            field = str(location[-1]) if location else "request"
            field_errors[field] = item.get("msg", "Invalid value")
    elif isinstance(message, dict):
        stable_code = str(message.get("code") or stable_code)
        text = str(message.get("message") or text)
        field_errors = message.get("field_errors") or {}
    return {"error": {"code": stable_code, "message": text,
                      "field_errors": field_errors, "request_id": request_id}}
