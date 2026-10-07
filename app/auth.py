"""API 키 인증.

`X-API-Key: <키>` 또는 `Authorization: Bearer <키>` 헤더로 키를 받습니다.
API_KEYS가 비어 있으면 인증을 끕니다 (로컬 개발용).
"""

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

# auto_error=False: 두 헤더 중 하나만 있어도 되도록 직접 검사합니다.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="발급받은 API 키")
bearer = HTTPBearer(auto_error=False, description="Authorization: Bearer <API 키>")


def _matches(candidate: str, keys: tuple[str, ...]) -> bool:
    # 타이밍 공격을 막기 위해 일치 여부와 상관없이 모든 키와 상수 시간 비교
    result = False
    for key in keys:
        result |= secrets.compare_digest(candidate.encode(), key.encode())
    return result


async def require_api_key(
    request: Request,
    header_key: str | None = Depends(api_key_header),
    bearer_cred: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    keys: tuple[str, ...] = request.app.state.api_keys
    if not keys:
        return
    candidate = header_key or (bearer_cred.credentials if bearer_cred else None)
    if not candidate or not _matches(candidate, keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효한 API 키가 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
