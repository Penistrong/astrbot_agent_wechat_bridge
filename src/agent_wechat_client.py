"""agent-wechat (1.x Native /v1 API) 客户端与 SSE 事件流封装。"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import mimetypes
import os
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, urlencode

import requests

TERMINAL_SEND_STATES = {
    "accepted",
    "sent",
    "server_accepted",
    "completed",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
    "needs_review",
}
SUCCESS_SEND_STATES = {
    "accepted",
    "sent",
    "server_accepted",
    "completed",
    "succeeded",
}
TERMINAL_JOB_STATES = {
    "accepted",
    "completed",
    "succeeded",
    "available",
    "failed",
    "partial",
    "cancelled",
    "expired",
    "needs_review",
}
SUCCESS_JOB_STATES = {
    "accepted",
    "completed",
    "succeeded",
    "available",
}


class AgentWeChatAPIError(RuntimeError):
    """桥接服务接口返回非成功状态时抛出。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "request_failed",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable


def encode_resource_id(value: str) -> str:
    """对路径中的资源 ID（包含 '/' 的 messageId 或 '@chatroom' 等）进行完整 URL 编码。"""
    raw = str(value or "").strip()
    if not raw or raw in {".", ".."}:
        raise ValueError(f"Invalid native resource ID: {value!r}")
    return quote(raw, safe="")


class WeChatClient:
    """对 agent-wechat 1.x (/v1) HTTP API 的封装。"""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        account_id: str | None = None,
        timeout: int = 15,
        verify_tls: bool = True,
    ) -> None:
        self.base_url = self._normalize_url(base_url)
        self.token = (token or "").strip() or None
        self.account_id = (account_id or "").strip() or None
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.headers: dict[str, str] = {}
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"
        self._thread_local = threading.local()
        self._account_lock = threading.Lock()

    @staticmethod
    def _normalize_url(base_url: str) -> str:
        url = (base_url or "").strip()
        if not url:
            url = "http://127.0.0.1:6174"
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        url = url.rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3].rstrip("/")
        return url

    @staticmethod
    def _qs(params: dict[str, Any]) -> str:
        items: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                str_val = "true" if value else "false"
            else:
                str_val = str(value)
            items.append((str(key), str_val))
        if not items:
            return ""
        return "?" + urlencode(items)

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=16,
                pool_maxsize=16,
                max_retries=0,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            self._thread_local.session = session
        return session

    def _build_headers(
        self,
        extra: dict[str, str] | None = None,
        *,
        json_body: bool = True,
    ) -> dict[str, str]:
        headers = dict(self.headers)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_body:
            headers.setdefault("Content-Type", "application/json")
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _raise_api_error(response: requests.Response) -> None:
        code = "request_failed"
        retryable = response.status_code >= 500
        try:
            payload = response.json()
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict):
                    if isinstance(err.get("code"), str) and err["code"]:
                        code = err["code"]
                    if isinstance(err.get("retryable"), bool):
                        retryable = err["retryable"]
                elif isinstance(err, str) and err:
                    code = err
        except Exception:
            pass
        raise AgentWeChatAPIError(
            f"HTTP {response.status_code} ({code}): {response.text}",
            status_code=response.status_code,
            code=code,
            retryable=retryable,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        req_headers = self._build_headers(headers, json_body=body is not None)
        response = self._session().request(
            method,
            url,
            json=body,
            headers=req_headers,
            timeout=self.timeout if timeout is None else timeout,
            verify=self.verify_tls,
        )
        if not response.ok:
            self._raise_api_error(response)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def _get(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> Any:
        return self._request_json("GET", path, headers=headers, timeout=timeout)

    def _post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> Any:
        return self._request_json(
            "POST",
            path,
            body={} if body is None else body,
            headers=headers,
            timeout=timeout,
        )

    def _put(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> Any:
        return self._request_json(
            "PUT",
            path,
            body={} if body is None else body,
            headers=headers,
            timeout=timeout,
        )

    def _patch(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> Any:
        return self._request_json(
            "PATCH",
            path,
            body={} if body is None else body,
            headers=headers,
            timeout=timeout,
        )

    def _delete(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> Any:
        return self._request_json(
            "DELETE",
            path,
            body=body,
            headers=headers,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Daemon & Account Management
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        result = self._get("/health")
        return result if isinstance(result, dict) else {}

    def status(self) -> dict[str, Any]:
        result = self._get("/v1/status")
        return result if isinstance(result, dict) else {}

    def list_accounts(self) -> list[dict[str, Any]]:
        result = self._get("/v1/accounts")
        if isinstance(result, dict):
            items = result.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    def create_account(
        self,
        *,
        account_id: str | None = None,
        alias: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if account_id:
            body["id"] = account_id
        if alias:
            body["alias"] = alias
        if not body:
            body["alias"] = "astrbot"
        result = self._post("/v1/accounts", body)
        return result if isinstance(result, dict) else {}

    def resolve_account_id(self, *, force_refresh: bool = False) -> str:
        if self.account_id and not force_refresh:
            return self.account_id

        with self._account_lock:
            if self.account_id and not force_refresh:
                return self.account_id

            accounts = self.list_accounts()
            if not accounts:
                raise AgentWeChatAPIError(
                    "agent-wechat 中尚未创建任何微信账号 Profile，请先在服务端执行 "
                    "`wx accounts create --alias bot` 或 `wx auth login --official`",
                    status_code=404,
                    code="account_not_found",
                )

            if self.account_id:
                for item in accounts:
                    if self.account_id in {
                        str(item.get("id") or ""),
                        str(item.get("alias") or ""),
                    }:
                        self.account_id = str(item["id"])
                        return self.account_id

            for predicate in (
                lambda a: bool(a.get("authenticated"))
                and a.get("connection") == "online",
                lambda a: bool(a.get("authenticated")),
                lambda _: True,
            ):
                for item in accounts:
                    candidate_id = str(item.get("id") or "").strip()
                    if candidate_id and predicate(item):
                        self.account_id = candidate_id
                        return self.account_id

            raise AgentWeChatAPIError(
                "无法解析有效的 agent-wechat accountId",
                status_code=404,
                code="account_not_found",
            )

    def _account_path(self, suffix: str = "", account_id: str | None = None) -> str:
        acct = encode_resource_id(account_id or self.resolve_account_id())
        if not suffix:
            return f"/v1/accounts/{acct}"
        if not suffix.startswith("/"):
            suffix = f"/{suffix}"
        return f"/v1/accounts/{acct}{suffix}"

    def get_account(self, account_id: str | None = None) -> dict[str, Any]:
        result = self._get(self._account_path("", account_id))
        if isinstance(result, dict) and result.get("id"):
            self.account_id = str(result["id"])
            return result
        return {}

    def capabilities(self, account_id: str | None = None) -> dict[str, Any]:
        result = self._get(self._account_path("/capabilities", account_id))
        return result if isinstance(result, dict) else {}

    def auth_status(self, account_id: str | None = None) -> dict[str, Any]:
        """获取账号认证与在线状态，并保持对旧版字段的向后兼容。"""
        acct = self.get_account(account_id)
        authenticated = bool(acct.get("authenticated"))
        connection = str(acct.get("connection") or "disconnected")
        own_username = acct.get("ownUsername")
        if authenticated and connection == "online":
            status_str = "logged_in"
        elif authenticated and connection == "reconnecting":
            status_str = "reconnecting"
        elif authenticated:
            status_str = "disconnected"
        else:
            status_str = "logged_out"

        return {
            **acct,
            "accountId": acct.get("id") or self.account_id,
            "status": status_str,
            "authenticated": authenticated,
            "connection": connection,
            "loggedInUser": own_username,
        }

    def connect(self, account_id: str | None = None) -> dict[str, Any]:
        result = self._post(self._account_path("/connect", account_id), {})
        return result if isinstance(result, dict) else {}

    def disconnect(self, account_id: str | None = None) -> dict[str, Any]:
        result = self._post(self._account_path("/auth/disconnect", account_id), {})
        return result if isinstance(result, dict) else {}

    def sync(self, account_id: str | None = None) -> dict[str, Any]:
        result = self._post(self._account_path("/sync", account_id), {})
        return result if isinstance(result, dict) else {}

    def start_login(
        self,
        account_id: str | None = None,
        *,
        strategy: str = "auto",
        allow_qr: bool = True,
        force_qr: bool = False,
        timeout_seconds: int = 300,
        computer_name: str = "astrbot",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = idempotency_key or str(uuid.uuid4())
        body = {
            "strategy": "qr" if force_qr else strategy,
            "allowQr": allow_qr,
            "forceQr": force_qr,
            "timeoutSeconds": max(30, min(900, int(timeout_seconds))),
            "computerName": computer_name,
            "qrRefresh": {"mode": "auto", "maxRefreshes": 3},
        }
        result = self._post(
            self._account_path("/auth/login", account_id),
            body,
            headers={"Idempotency-Key": key},
        )
        return result if isinstance(result, dict) else {}

    def login(self, account_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.start_login(account_id=account_id, **kwargs)

    def logout(
        self,
        account_id: str | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = idempotency_key or str(uuid.uuid4())
        result = self._post(
            self._account_path("/logout", account_id),
            {},
            headers={"Idempotency-Key": key},
        )
        return result if isinstance(result, dict) else {}

    def get_operation(
        self,
        operation_id: str,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        op_id = encode_resource_id(operation_id)
        result = self._get(self._account_path(f"/operations/{op_id}", account_id))
        return result if isinstance(result, dict) else {}

    def cancel_operation(
        self,
        operation_id: str,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        op_id = encode_resource_id(operation_id)
        result = self._delete(self._account_path(f"/operations/{op_id}", account_id))
        return result if isinstance(result, dict) else {}

    def get_login_qr_png(
        self,
        operation_id: str,
        generation: int | None = None,
        account_id: str | None = None,
    ) -> bytes:
        op_id = encode_resource_id(operation_id)
        qs = self._qs({"generation": generation})
        path = self._account_path(f"/operations/{op_id}/qr{qs}", account_id)
        response = self._session().get(
            f"{self.base_url}{path}",
            headers=self._build_headers(json_body=False),
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        if not response.ok:
            self._raise_api_error(response)
        return response.content

    # ------------------------------------------------------------------
    # Threads, Contacts, Groups & Messages
    # ------------------------------------------------------------------

    def list_threads(
        self,
        limit: int = 50,
        cursor: str | None = None,
        *,
        search: str | None = None,
        kind: str | None = None,
        account_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        qs = self._qs(
            {
                "limit": max(1, min(200, int(limit))),
                "cursor": cursor,
                "search": search,
                "kind": kind,
            }
        )
        result = self._get(
            self._account_path(f"/threads{qs}", account_id),
            timeout=timeout,
        )
        return result if isinstance(result, dict) else {"items": [], "nextCursor": None}

    def get_thread(
        self,
        thread_id: str,
        account_id: str | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        tid = encode_resource_id(thread_id)
        result = self._get(
            self._account_path(f"/threads/{tid}", account_id),
            timeout=timeout,
        )
        return result if isinstance(result, dict) else None

    def mark_thread_read(
        self,
        thread_id: str,
        expected_version: str,
        account_id: str | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        tid = encode_resource_id(thread_id)
        result = self._post(
            self._account_path(f"/threads/{tid}/read-state", account_id),
            {"expectedVersion": expected_version},
            timeout=timeout,
        )
        return result if isinstance(result, dict) else {}

    def list_thread_messages(
        self,
        thread_id: str,
        limit: int = 20,
        cursor: str | None = None,
        account_id: str | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        tid = encode_resource_id(thread_id)
        qs = self._qs(
            {
                "limit": max(1, min(200, int(limit))),
                "cursor": cursor,
            }
        )
        result = self._get(
            self._account_path(f"/threads/{tid}/messages{qs}", account_id),
            timeout=timeout,
        )
        return result if isinstance(result, dict) else {"items": [], "nextCursor": None}

    def get_message(
        self,
        message_id: str,
        account_id: str | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        mid = encode_resource_id(message_id)
        result = self._get(
            self._account_path(f"/messages/{mid}", account_id),
            timeout=timeout,
        )
        return result if isinstance(result, dict) else {}

    def search_messages(
        self,
        query: str,
        limit: int = 20,
        cursor: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        qs = self._qs(
            {
                "query": query,
                "limit": max(1, min(200, int(limit))),
                "cursor": cursor,
            }
        )
        result = self._get(self._account_path(f"/messages{qs}", account_id))
        return result if isinstance(result, dict) else {"items": [], "nextCursor": None}

    def list_contacts(
        self,
        limit: int = 50,
        cursor: str | None = None,
        *,
        search: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        qs = self._qs(
            {
                "limit": max(1, min(200, int(limit))),
                "cursor": cursor,
                "search": search,
            }
        )
        result = self._get(self._account_path(f"/contacts{qs}", account_id))
        return result if isinstance(result, dict) else {"items": [], "nextCursor": None}

    def get_contact(
        self,
        username: str,
        account_id: str | None = None,
    ) -> dict[str, Any] | None:
        uid = encode_resource_id(username)
        try:
            result = self._get(self._account_path(f"/contacts/{uid}", account_id))
        except AgentWeChatAPIError as exc:
            if exc.status_code == 404:
                return None
            raise
        return result if isinstance(result, dict) else None

    def get_group(
        self,
        room_id: str,
        account_id: str | None = None,
    ) -> dict[str, Any] | None:
        rid = encode_resource_id(room_id)
        try:
            result = self._get(self._account_path(f"/groups/{rid}", account_id))
        except AgentWeChatAPIError as exc:
            if exc.status_code == 404:
                return None
            raise
        return result if isinstance(result, dict) else None

    def list_group_members(
        self,
        room_id: str,
        limit: int = 200,
        cursor: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        rid = encode_resource_id(room_id)
        qs = self._qs(
            {
                "limit": max(1, min(200, int(limit))),
                "cursor": cursor,
            }
        )
        result = self._get(self._account_path(f"/groups/{rid}/members{qs}", account_id))
        return result if isinstance(result, dict) else {"items": [], "nextCursor": None}

    # ------------------------------------------------------------------
    # Events & Cursor Management
    # ------------------------------------------------------------------

    def list_events(
        self,
        after: str = "0",
        limit: int = 100,
        account_id: str | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        safe_after = str(after or "0").strip()
        if not safe_after.isdigit():
            safe_after = "0"
        qs = self._qs(
            {
                "after": safe_after,
                "limit": max(1, min(200, int(limit))),
            }
        )
        result = self._get(
            self._account_path(f"/events{qs}", account_id),
            timeout=timeout,
        )
        return (
            result
            if isinstance(result, dict)
            else {"items": [], "nextCursor": safe_after}
        )

    def ack_events(
        self,
        consumer_id: str,
        cursor: str,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        safe_cursor = str(cursor or "0").strip()
        if not safe_cursor.isdigit():
            return {}
        cid = encode_resource_id(consumer_id)
        result = self._post(
            self._account_path(f"/consumers/{cid}/ack", account_id),
            {"cursor": safe_cursor},
        )
        return result if isinstance(result, dict) else {}

    def build_events_stream_url(
        self,
        after: str = "0",
        account_id: str | None = None,
    ) -> str:
        safe_after = str(after or "0").strip()
        if not safe_after.isdigit():
            safe_after = "0"
        path = self._account_path(
            f"/events/stream{self._qs({'after': safe_after})}",
            account_id,
        )
        return f"{self.base_url}{path}"

    def build_events_ws_url(
        self,
        after: str = "0",
        account_id: str | None = None,
    ) -> str:
        """兼容测试与调用方：返回 /v1 SSE 事件流地址。"""
        acct = account_id or self.account_id or "default"
        safe_after = str(after or "0").strip()
        if not safe_after.isdigit():
            safe_after = "0"
        path = f"/v1/accounts/{encode_resource_id(acct)}/events/stream{self._qs({'after': safe_after})}"
        return f"{self.base_url}{path}"

    # ------------------------------------------------------------------
    # Media Download & Transfer Jobs
    # ------------------------------------------------------------------

    def list_message_media(
        self,
        message_id: str,
        account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        mid = encode_resource_id(message_id)
        result = self._get(self._account_path(f"/messages/{mid}/media", account_id))
        if isinstance(result, dict):
            items = result.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    def start_media_transfer(
        self,
        message_id: str,
        quality: str = "best",
        asset_id: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        mid = encode_resource_id(message_id)
        body = {
            "messageId": message_id,
            "assetId": asset_id,
            "quality": quality,
        }
        result = self._post(
            self._account_path(f"/messages/{mid}/media-transfers", account_id),
            body,
        )
        return result if isinstance(result, dict) else {}

    def get_media_asset(
        self,
        asset_id: str,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        aid = encode_resource_id(asset_id)
        result = self._get(self._account_path(f"/media/{aid}", account_id))
        return result if isinstance(result, dict) else {}

    def get_media_content(
        self,
        asset_id: str,
        *,
        representation: str = "original",
        account_id: str | None = None,
        timeout: float | None = 60.0,
    ) -> tuple[bytes, str, dict[str, str]]:
        aid = encode_resource_id(asset_id)
        qs = self._qs(
            {"representation": "preview" if representation == "preview" else None}
        )
        path = self._account_path(f"/media/{aid}/content{qs}", account_id)
        response = self._session().get(
            f"{self.base_url}{path}",
            headers=self._build_headers(json_body=False),
            timeout=self.timeout if timeout is None else timeout,
            verify=self.verify_tls,
        )
        if not response.ok:
            self._raise_api_error(response)
        content_type = (
            (response.headers.get("content-type") or "application/octet-stream")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        meta = {
            "etag": response.headers.get("etag") or "",
            "source_sha256": response.headers.get("x-source-sha256") or "",
        }
        return response.content, content_type, meta

    def get_job(
        self,
        job_id: str,
        account_id: str | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        jid = encode_resource_id(job_id)
        result = self._get(
            self._account_path(f"/jobs/{jid}", account_id),
            timeout=timeout,
        )
        return result if isinstance(result, dict) else {}

    def wait_media_job(
        self,
        initial_job: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        job = dict(initial_job or {})
        job_id = str(job.get("id") or "").strip()
        if not job_id:
            return job

        deadline = time.monotonic() + max(0.5, float(timeout_seconds))
        delay = 0.25
        while str(job.get("state") or "") not in TERMINAL_JOB_STATES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(delay, remaining))
            try:
                job = self.get_job(
                    job_id,
                    account_id=account_id,
                    timeout=max(1.0, min(15.0, deadline - time.monotonic())),
                )
            except Exception:
                break
            delay = min(delay * 1.8, 1.0)
        return job

    @staticmethod
    def _select_available_asset(
        assets: list[dict[str, Any]],
        msg_type: int,
        quality: str = "best",
    ) -> dict[str, Any] | None:
        if msg_type == 34:
            preferred = ["normal", "original", "standard", "encoded"]
        elif msg_type == 43:
            preferred = ["normal", "original", "standard", "thumbnail"]
        else:
            preferred = ["original", "standard", "normal", "thumbnail", "encoded"]

        def rank(asset: dict[str, Any]) -> int:
            variant = str(asset.get("actualVariant") or asset.get("variant") or "")
            try:
                return preferred.index(variant)
            except ValueError:
                return len(preferred)

        available = [a for a in assets if a.get("state") == "available"]
        if not available:
            return None

        available.sort(key=rank)
        if quality != "best":
            for asset in available:
                variant = str(asset.get("actualVariant") or asset.get("variant") or "")
                if variant == quality:
                    return asset
        return available[0]

    def download_message_media(
        self,
        message_id: str,
        *,
        msg_type: int = 0,
        quality: str = "best",
        wait_seconds: float = 20.0,
        account_id: str | None = None,
    ) -> tuple[bytes, str, str] | None:
        """下载消息关联的媒体资源（自动发起按需传输并处理 WXGF 预览图）。"""
        acct = account_id or self.resolve_account_id()
        assets = self.list_message_media(message_id, account_id=acct)
        asset = self._select_available_asset(assets, msg_type, quality)

        if asset is None:
            transfer_quality = (
                "normal" if quality == "best" and msg_type in {34, 43} else quality
            )
            in_flight_job_id: str | None = None
            for item in assets:
                if item.get("jobId") and item.get("state") not in {
                    "recalled",
                    "expired",
                    "unsupported",
                }:
                    in_flight_job_id = str(item["jobId"])
                    break

            if in_flight_job_id:
                job = self.get_job(in_flight_job_id, account_id=acct)
                self.wait_media_job(
                    job, timeout_seconds=wait_seconds, account_id=acct
                )
            else:
                has_terminal_unavailable = bool(assets) and all(
                    item.get("state") in {"expired", "unsupported", "recalled"}
                    for item in assets
                )
                if not has_terminal_unavailable:
                    job = self.start_media_transfer(
                        message_id,
                        quality=transfer_quality,
                        account_id=acct,
                    )
                    self.wait_media_job(
                        job, timeout_seconds=wait_seconds, account_id=acct
                    )

            assets = self.list_message_media(message_id, account_id=acct)
            asset = self._select_available_asset(assets, msg_type, quality)

        if asset is None or not asset.get("id"):
            return None

        asset_id = str(asset["id"])
        raw_bytes, mime_type, _ = self.get_media_content(
            asset_id,
            representation="original",
            account_id=acct,
        )
        filename = str(asset.get("filename") or "").strip()

        if mime_type == "image/x-wxgf" or (
            isinstance(asset.get("preview"), dict) and mime_type not in {
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/webp",
            }
        ):
            try:
                preview_bytes, preview_mime, _ = self.get_media_content(
                    asset_id,
                    representation="preview",
                    account_id=acct,
                )
                if preview_bytes and preview_mime.startswith("image/"):
                    raw_bytes = preview_bytes
                    mime_type = preview_mime
                    preview_meta = asset.get("preview")
                    if isinstance(preview_meta, dict) and preview_meta.get("filename"):
                        filename = str(preview_meta["filename"])
            except Exception:
                pass

        if not filename:
            ext = mimetypes.guess_extension(mime_type) or ".bin"
            if mime_type == "image/jpeg":
                ext = ".jpg"
            filename = f"media_{asset_id[:12]}{ext}"

        return raw_bytes, mime_type, filename

    # ------------------------------------------------------------------
    # Outbound Sends (Text, Mentions, Quotes, Shares, Media Uploads)
    # ------------------------------------------------------------------

    def upload_media(
        self,
        data: bytes,
        filename: str,
        mime_type: str = "application/octet-stream",
        account_id: str | None = None,
        *,
        timeout: float | None = 120.0,
    ) -> dict[str, Any]:
        safe_name = (
            os.path.basename(filename or "attachment.bin")
            .replace("\r", "_")
            .replace("\n", "_")
            .replace("/", "_")
            .replace("\\", "_")
        )[:240] or "attachment.bin"
        path = self._account_path("/uploads", account_id)
        headers = self._build_headers(json_body=False)
        files = {"file": (safe_name, data, mime_type or "application/octet-stream")}
        response = self._session().post(
            f"{self.base_url}{path}",
            headers=headers,
            files=files,
            timeout=self.timeout if timeout is None else timeout,
            verify=self.verify_tls,
        )
        if not response.ok:
            self._raise_api_error(response)
        result = response.json()
        return result if isinstance(result, dict) else {}

    def get_send(
        self,
        send_id: str,
        account_id: str | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        sid = encode_resource_id(send_id)
        result = self._get(
            self._account_path(f"/sends/{sid}", account_id),
            timeout=timeout,
        )
        return result if isinstance(result, dict) else {}

    def get_send_by_key(
        self,
        key: str,
        account_id: str | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        qs = self._qs({"key": key})
        result = self._get(
            self._account_path(f"/sends/by-idempotency-key{qs}", account_id),
            timeout=timeout,
        )
        return result if isinstance(result, dict) else {}

    def _wait_send_intent(
        self,
        initial_intent: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        intent = dict(initial_intent or {})
        send_id = str(intent.get("id") or "").strip()
        if not send_id:
            return intent

        deadline = time.monotonic() + max(0.5, float(timeout_seconds))
        delay = 0.2
        while str(intent.get("state") or "") in {"queued", "submitting"}:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(delay, remaining))
            try:
                intent = self.get_send(
                    send_id,
                    account_id=account_id,
                    timeout=max(1.0, min(10.0, deadline - time.monotonic())),
                )
            except Exception:
                break
            delay = min(delay * 1.8, 1.0)
        return intent

    def send_text(
        self,
        thread_id: str,
        text: str,
        *,
        idempotency_key: str | None = None,
        mentions: dict[str, Any] | None = None,
        quote: dict[str, str] | None = None,
        account_id: str | None = None,
        timeout: float | tuple[float, float] | None = 30.0,
    ) -> dict[str, Any]:
        acct = account_id or self.resolve_account_id()
        key = idempotency_key or str(uuid.uuid4())
        body: dict[str, Any] = {
            "threadId": thread_id,
            "text": text,
        }
        if quote:
            body["kind"] = "text"
            body["quote"] = {
                "messageId": str(quote["messageId"]),
                "serverId": str(quote["serverId"]),
            }
        elif mentions:
            body["kind"] = "text"
            body["mentions"] = mentions

        try:
            intent = self._post(
                self._account_path("/sends", acct),
                body,
                headers={"Idempotency-Key": key},
                timeout=timeout,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            intent = self.get_send_by_key(key, account_id=acct, timeout=10.0)
        except AgentWeChatAPIError as exc:
            if exc.status_code is not None and exc.status_code >= 500:
                try:
                    intent = self.get_send_by_key(key, account_id=acct, timeout=10.0)
                except Exception:
                    raise exc
            else:
                raise

        wait_budget = 30.0 if isinstance(timeout, (int, float)) else 30.0
        intent = self._wait_send_intent(
            intent if isinstance(intent, dict) else {},
            timeout_seconds=wait_budget,
            account_id=acct,
        )
        state = str(intent.get("state") or "")
        if state in SUCCESS_SEND_STATES:
            return {"success": True, **intent}
        error_code = str(intent.get("errorCode") or state or "send_failed")
        return {"success": False, "error": error_code, **intent}

    def send_share(
        self,
        thread_id: str,
        title: str,
        url: str,
        *,
        description: str | None = None,
        thumbnail_url: str | None = None,
        idempotency_key: str | None = None,
        account_id: str | None = None,
        timeout: float | tuple[float, float] | None = 30.0,
    ) -> dict[str, Any]:
        acct = account_id or self.resolve_account_id()
        key = idempotency_key or str(uuid.uuid4())
        body: dict[str, Any] = {
            "kind": "share",
            "threadId": thread_id,
            "title": title,
            "url": url,
        }
        if description:
            body["description"] = description
        if thumbnail_url:
            body["thumbnailUrl"] = thumbnail_url

        intent = self._post(
            self._account_path("/sends", acct),
            body,
            headers={"Idempotency-Key": key},
            timeout=timeout,
        )
        intent = self._wait_send_intent(
            intent if isinstance(intent, dict) else {},
            timeout_seconds=30.0,
            account_id=acct,
        )
        state = str(intent.get("state") or "")
        if state in SUCCESS_SEND_STATES:
            return {"success": True, **intent}
        error_code = str(intent.get("errorCode") or state or "send_share_failed")
        return {"success": False, "error": error_code, **intent}

    def send_media_bytes(
        self,
        thread_id: str,
        data: bytes,
        *,
        filename: str,
        mime_type: str,
        kind: str,
        video_quality: str | None = None,
        idempotency_key: str | None = None,
        account_id: str | None = None,
        timeout: float | tuple[float, float] | None = 120.0,
    ) -> dict[str, Any]:
        acct = account_id or self.resolve_account_id()
        key = idempotency_key or str(uuid.uuid4())
        upload_timeout = timeout if isinstance(timeout, (int, float)) else 120.0
        uploaded = self.upload_media(
            data,
            filename=filename,
            mime_type=mime_type,
            account_id=acct,
            timeout=upload_timeout,
        )
        source_id = str(uploaded.get("id") or "").strip()
        if not source_id:
            return {"success": False, "error": "upload_source_id_missing"}

        body: dict[str, Any] = {
            "threadId": thread_id,
            "kind": kind,
            "sourceId": source_id,
            "standardSourceId": None,
            "thumbnailSourceId": None,
            "playSeconds": None,
            "durationMs": None,
        }
        if kind == "video":
            body["videoQuality"] = video_quality or "normal"

        try:
            job = self._post(
                self._account_path("/sends", acct),
                body,
                headers={"Idempotency-Key": key},
                timeout=timeout,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            job = self.get_send_by_key(key, account_id=acct, timeout=10.0)

        wait_budget = float(upload_timeout) if upload_timeout else 120.0
        final_job = self.wait_media_job(
            job if isinstance(job, dict) else {},
            timeout_seconds=max(30.0, wait_budget),
            account_id=acct,
        )
        state = str(final_job.get("state") or "")
        if state in SUCCESS_JOB_STATES:
            return {"success": True, **final_job}
        reason = str(final_job.get("reason") or state or "media_send_failed")
        return {"success": False, "error": reason, **final_job}

    def recall_message(
        self,
        message_id: str,
        server_id: str,
        *,
        idempotency_key: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        acct = account_id or self.resolve_account_id()
        mid = encode_resource_id(message_id)
        key = idempotency_key or str(uuid.uuid4())
        result = self._post(
            self._account_path(f"/messages/{mid}/recall", acct),
            {"serverId": str(server_id)},
            headers={"Idempotency-Key": key},
        )
        return result if isinstance(result, dict) else {}

    def send_message(
        self,
        payload: dict[str, Any],
        timeout: float | tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        """统一消息发送入口：将插件内部 payload 转换为 /v1/accounts/{a}/uploads + /sends。"""
        thread_id = str(payload.get("threadId") or payload.get("chatId") or "").strip()
        if not thread_id:
            raise ValueError("send_message requires threadId or chatId")

        account_id = payload.get("accountId") or self.account_id
        key = str(payload.get("idempotencyKey") or uuid.uuid4())

        if payload.get("kind") == "share" or "share" in payload:
            share = payload.get("share") if isinstance(payload.get("share"), dict) else payload
            return self.send_share(
                thread_id,
                title=str(share.get("title") or "分享链接"),
                url=str(share.get("url") or ""),
                description=share.get("description"),
                thumbnail_url=share.get("thumbnailUrl"),
                idempotency_key=key,
                account_id=account_id,
                timeout=timeout,
            )

        if "text" in payload and payload.get("text") is not None:
            return self.send_text(
                thread_id,
                str(payload["text"]),
                idempotency_key=key,
                mentions=payload.get("mentions")
                if isinstance(payload.get("mentions"), dict)
                else None,
                quote=payload.get("quote")
                if isinstance(payload.get("quote"), dict)
                else None,
                account_id=account_id,
                timeout=timeout,
            )

        for media_key in ("image", "video", "voice", "file"):
            media_obj = payload.get(media_key)
            if not isinstance(media_obj, dict):
                continue

            raw_bytes: bytes | None = media_obj.get("bytes")
            if raw_bytes is None and media_obj.get("data"):
                raw_bytes = base64.b64decode(str(media_obj["data"]))
            elif raw_bytes is None and media_obj.get("path"):
                with open(str(media_obj["path"]), "rb") as handle:
                    raw_bytes = handle.read()

            if not raw_bytes:
                return {"success": False, "error": f"empty_{media_key}_payload"}

            mime_type = str(
                media_obj.get("mimeType")
                or (
                    "image/png"
                    if media_key == "image"
                    else "video/mp4"
                    if media_key == "video"
                    else "audio/wav"
                    if media_key == "voice"
                    else "application/octet-stream"
                )
            )
            default_ext = (
                ".png"
                if media_key == "image"
                else ".mp4"
                if media_key == "video"
                else ".wav"
                if media_key == "voice"
                else ".bin"
            )
            filename = str(
                media_obj.get("filename") or f"{media_key}{default_ext}"
            )
            selected_kind = str(media_obj.get("kind") or media_key)
            return self.send_media_bytes(
                thread_id,
                raw_bytes,
                filename=filename,
                mime_type=mime_type,
                kind=selected_kind,
                video_quality=media_obj.get("videoQuality"),
                idempotency_key=key,
                account_id=account_id,
                timeout=timeout,
            )

        raise ValueError(f"Unsupported send_message payload keys: {list(payload.keys())}")


Callback = Callable[..., Any | Awaitable[Any]]


class WeChatEventStreamClient:
    """基于 /v1/accounts/{account}/events/stream (SSE) 的可重连事件流客户端。"""

    def __init__(
        self,
        stream_url: str | Callable[[], str],
        *,
        token: str | None = None,
        verify_tls: bool = True,
        on_open: Callback | None = None,
        on_message: Callback | None = None,
        on_close: Callback | None = None,
        on_error: Callback | None = None,
        idle_timeout: float = 45.0,
    ) -> None:
        self.stream_url = stream_url
        self.token = (token or "").strip() or None
        self.verify_tls = verify_tls
        self.on_open = on_open
        self.on_message = on_message
        self.on_close = on_close
        self.on_error = on_error
        self.idle_timeout = idle_timeout

    @staticmethod
    async def _maybe_call(callback: Callback | None, *args: Any) -> None:
        if callback is None:
            return
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    def _resolve_url(self) -> str:
        if callable(self.stream_url):
            return self.stream_url()
        return str(self.stream_url)

    def _read_sse_stream_sync(
        self,
        url: str,
        stop_event: asyncio.Event,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[tuple[str, Any]],
    ) -> None:
        headers = {"Accept": "text/event-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            with requests.get(
                url,
                headers=headers,
                stream=True,
                timeout=(15.0, self.idle_timeout),
                verify=self.verify_tls,
            ) as response:
                if not response.ok:
                    WeChatClient._raise_api_error(response)

                loop.call_soon_threadsafe(queue.put_nowait, ("open", None))

                data_lines: list[str] = []
                event_type = ""
                event_cursor = ""

                for raw_line in response.iter_lines(decode_unicode=False):
                    if stop_event.is_set():
                        break
                    if raw_line is None:
                        continue
                    line = (
                        raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                        if isinstance(raw_line, bytes)
                        else str(raw_line).rstrip("\r\n")
                    )

                    if not line:
                        if data_lines:
                            raw_payload = "\n".join(data_lines)
                            try:
                                parsed = json.loads(raw_payload)
                                if isinstance(parsed, dict):
                                    if event_cursor and "cursor" not in parsed:
                                        parsed["cursor"] = event_cursor
                                    if event_type and "sseEvent" not in parsed:
                                        parsed["sseEvent"] = event_type
                                    raw_payload = json.dumps(
                                        parsed, ensure_ascii=False
                                    )
                            except Exception:
                                pass
                            loop.call_soon_threadsafe(
                                queue.put_nowait, ("message", raw_payload)
                            )
                        data_lines = []
                        event_type = ""
                        event_cursor = ""
                        continue

                    if line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        item = line[5:]
                        if item.startswith(" "):
                            item = item[1:]
                        data_lines.append(item)
                    elif line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("id:"):
                        event_cursor = line[3:].strip()
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("close", None))

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        reconnect_delay = 1.0
        loop = asyncio.get_running_loop()

        while not stop_event.is_set():
            opened = False
            queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
            try:
                url = self._resolve_url()
            except Exception as exc:
                await self._maybe_call(self.on_error, exc)
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=reconnect_delay
                    )
                except asyncio.TimeoutError:
                    pass
                reconnect_delay = min(reconnect_delay * 2, 15.0)
                continue

            reader_future = loop.run_in_executor(
                None,
                self._read_sse_stream_sync,
                url,
                stop_event,
                loop,
                queue,
            )

            try:
                while not stop_event.is_set():
                    get_task = asyncio.create_task(queue.get())
                    stop_task = asyncio.create_task(stop_event.wait())
                    done, pending = await asyncio.wait(
                        {get_task, stop_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()

                    if stop_task in done:
                        break

                    kind, value = get_task.result()
                    if kind == "open":
                        opened = True
                        reconnect_delay = 1.0
                        await self._maybe_call(self.on_open)
                    elif kind == "message":
                        await self._maybe_call(self.on_message, value)
                    elif kind == "error":
                        await self._maybe_call(self.on_error, value)
                    elif kind == "close":
                        break
            finally:
                if opened:
                    await self._maybe_call(self.on_close)
                with contextlib_suppress():
                    await asyncio.wait_for(reader_future, timeout=2.0)

            if stop_event.is_set():
                break

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=reconnect_delay)
            except asyncio.TimeoutError:
                pass
            reconnect_delay = min(reconnect_delay * 2, 15.0)


class contextlib_suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return True


WeChatEventWebSocketClient = WeChatEventStreamClient
