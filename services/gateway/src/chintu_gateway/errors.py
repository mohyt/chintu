class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable=False, details=None):
        self.status = status
        self.body = {"error": {"code": code, "message": message,
                               "retryable": retryable, "details": details or {}}}
        super().__init__(message)


def unavailable():
    return ApiError(501, "unsupported_capability", "This capability is not implemented.")
