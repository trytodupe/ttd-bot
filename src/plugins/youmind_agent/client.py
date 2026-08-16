from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Self

import httpx


class YouMindAPIError(RuntimeError):
    def __init__(self, operation: str, status_code: int, detail: str):
        self.operation = operation
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"YouMind {operation} failed ({status_code}): {detail}")


async def _file_chunks(
    path: Path, chunk_size: int = 1024 * 1024
) -> AsyncIterator[bytes]:
    with path.open("rb") as handle:
        while chunk := await asyncio.to_thread(handle.read, chunk_size):
            yield chunk


class YouMindClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://youmind.com",
        proxy: str = "",
        request_timeout: float = 120.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("YouMind API key is empty")
        self._base_url = base_url.rstrip("/")
        self._proxy = proxy or None
        self._request_timeout = request_timeout
        self._client = httpx.AsyncClient(
            headers={
                "x-api-key": api_key.strip(),
                "Content-Type": "application/json",
                "x-use-camel-case": "true",
            },
            proxy=self._proxy,
            timeout=httpx.Timeout(request_timeout, connect=30.0),
            follow_redirects=True,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(self, operation: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._client.post(
                f"{self._base_url}/openapi/v1/{operation}",
                json=payload or {},
            )
        except httpx.HTTPError as exc:
            raise YouMindAPIError(operation, 0, str(exc)) from exc
        if response.is_error:
            detail = response.text[:1000]
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = str(body.get("message") or body.get("error") or body)[
                        :1000
                    ]
            except ValueError:
                pass
            raise YouMindAPIError(operation, response.status_code, detail)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise YouMindAPIError(
                operation, response.status_code, "invalid JSON response"
            ) from exc

    async def create_board(self, name: str) -> dict[str, Any]:
        result = await self.call("createBoard", {"name": name[:255]})
        if not isinstance(result, dict) or not result.get("id"):
            raise YouMindAPIError(
                "createBoard", 200, "response did not contain a board ID"
            )
        return result

    async def create_document(
        self, board_id: str, title: str, content: str
    ) -> dict[str, Any]:
        result = await self.call(
            "createDocument",
            {"boardId": board_id, "title": title[:255], "content": content},
        )
        if not isinstance(result, dict) or not result.get("id"):
            raise YouMindAPIError(
                "createDocument", 200, "response did not contain a file ID"
            )
        return result

    async def upload_file(
        self,
        *,
        board_id: str,
        path: Path,
        title: str,
        sha256: str,
        mime_type: str,
    ) -> dict[str, Any]:
        signed = await self.call(
            "genSignedPutUrlIfNotExist",
            {"hash": sha256, "mimeType": mime_type},
        )
        if not isinstance(signed, dict):
            raise YouMindAPIError("genSignedPutUrlIfNotExist", 200, "invalid response")
        upload_url = signed.get("uploadUrl")
        if upload_url:
            try:
                async with httpx.AsyncClient(
                    proxy=self._proxy,
                    timeout=httpx.Timeout(self._request_timeout, connect=30.0),
                    follow_redirects=True,
                ) as upload_client:
                    response = await upload_client.put(
                        str(upload_url),
                        headers={
                            "Content-Type": mime_type,
                            "Content-Length": str(path.stat().st_size),
                        },
                        content=_file_chunks(path),
                    )
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                raise YouMindAPIError("presignedPut", 0, str(exc)) from exc
        cdn_url = signed.get("cdnUrl")
        if not isinstance(cdn_url, str):
            raise YouMindAPIError(
                "genSignedPutUrlIfNotExist", 200, "response did not contain cdnUrl"
            )
        result = await self.call(
            "replace",
            {
                "boardId": board_id,
                "title": title[:2048],
                "hash": sha256,
                "mimeType": mime_type,
                "cdnUrl": cdn_url,
            },
        )
        if not isinstance(result, dict) or not result.get("id"):
            raise YouMindAPIError("replace", 200, "response did not contain a file ID")
        return result
