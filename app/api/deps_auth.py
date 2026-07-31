from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Header


@dataclass(frozen=True)
class AuthContext:
    tenant_id: UUID
    user_id: UUID


def authenticated_identity(
    tenant_id: Annotated[UUID, Header(alias="X-Tenant-ID")],
    user_id: Annotated[UUID, Header(alias="X-User-ID")],
) -> AuthContext:
    """Trusted host-app identity boundary for the M3 API."""
    return AuthContext(tenant_id=tenant_id, user_id=user_id)
