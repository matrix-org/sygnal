from typing import Optional

from google.auth.transport.requests import Request

class Credentials:
    token = "token"

    def refresh(self, request: Request) -> None: ...
    @staticmethod
    def from_service_account_file(
        service_account_file: str,
        scopes: Optional[list[str]] = None,
        request: Optional[str] = None,
        quota_project_id: Optional[int] = None,
        default_scopes: Optional[list[str]] = None,
    ) -> Credentials: ...
