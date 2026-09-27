import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import get_settings

API_KEY_HEADER = "X-API-Key"

_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def api_key_dependency(setting_name: str) -> Callable[[str | None], None]:
    """Build a dependency that checks X-API-Key against one setting.

    Add more keys by pointing this at another Settings field, e.g.
    require_admin_key = api_key_dependency("admin_api_key").
    """

    def dependency(
        provided: Annotated[str | None, Security(_api_key_header)] = None,
    ) -> None:
        expected: str | None = getattr(get_settings(), setting_name, None)
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{setting_name} is not configured",
            )
        # Encode first: compare_digest rejects str inputs holding non-ASCII.
        if not provided or not secrets.compare_digest(
            provided.encode("utf-8"), expected.encode("utf-8")
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Missing or invalid {API_KEY_HEADER}",
            )

    return dependency


require_ingest_key = api_key_dependency("ingest_api_key")
