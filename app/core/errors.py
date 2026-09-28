from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(
        self,
        error: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ):
        self.error = error
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


def error_response(
    error: str,
    message: str,
    status_code: int = status.HTTP_400_BAD_REQUEST,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    content = {
        "error": error,
        "message": message,
        "details": details or {},
    }
    return JSONResponse(status_code=status_code, content=content)
