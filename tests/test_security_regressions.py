"""Security regression tests for recent hardening fixes."""

from __future__ import annotations

import collections
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from fastapi import HTTPException

import agent.loop_mixins.planning as planning_module
from tools.admin import api_facade as admin_api
from tools.admin import auth as admin_auth
import runtime.config_manager as config_manager
from agent.loop import AgentLoop
from bus.events import InboundMessage
from bus.queue import MessageBus
from eval.benchmark import EvalBenchmarkManager
from llm.base import LLMResponse, ToolCallRequest
from tools.base import Tool
from tools.coding import ExecTool
from tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _disable_internal_planning(monkeypatch):
    monkeypatch.setattr(
        planning_module,
        "INTERNAL_PLANNING_POLICY",
        {"mode": "off", "auto": {}},
    )


def _make_request(
    *,
    host: str = "127.0.0.1",
    origin: str = "http://localhost:5173",
    auth: str = "",
    cookie: str = "",
    path: str = "/config",
    method: str = "GET",
    scheme: str = "http",
    extra_headers: dict[str, str] | None = None,
) -> Request:
    headers = []
    if origin:
        headers.append((b"origin", origin.encode("utf-8")))
    if auth:
        headers.append((b"authorization", auth.encode("utf-8")))
    if cookie:
        headers.append((b"cookie", cookie.encode("utf-8")))
    for key, value in (extra_headers or {}).items():
        if not key or value is None:
            continue
        headers.append((str(key).encode("utf-8"), str(value).encode("utf-8")))
    scope = {
        "type": "http",
        "method": method,
        "scheme": scheme,
        "path": path,
        "headers": headers,
        "client": (host, 12345),
    }
    return Request(scope)


class _FakeConfig:
    def __init__(self, data: dict):
        self.data = data
        self.set_many_calls = []

    def get(self, key_path: str, default=None):
        cur = self.data
        for k in key_path.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur

    def set_many(self, updates: dict) -> None:
        self.set_many_calls.append(dict(updates))


class _DummyWs:
    def __init__(self):
        self.accepted = False
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        self.messages.append(message)


class _DummyAuthWs:
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
        host: str = "127.0.0.1",
    ):
        self.headers = headers or {}
        self.query_params = query_params or {}
        self.client = SimpleNamespace(host=host)
        self.state = SimpleNamespace()
        self.closed = None

    async def close(self, *, code: int, reason: str = ""):
        self.closed = (code, reason)


class _DummyTool(Tool):
    def __init__(self, name: str, owner_only: bool = False, provider: str = "core", bypass_release_gate: bool = False):
        self._name = name
        self._owner_only = owner_only
        self._provider = provider
        self._bypass_release_gate = bypass_release_gate

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def owner_only(self) -> bool:
        return self._owner_only

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def bypass_release_gate(self) -> bool:
        return self._bypass_release_gate

    async def execute(self, **kwargs) -> str:
        return "ok"


class _CountingTool(_DummyTool):
    def __init__(self, name: str, owner_only: bool = False, provider: str = "core", bypass_release_gate: bool = False):
        super().__init__(name, owner_only, provider=provider, bypass_release_gate=bypass_release_gate)
        self.calls = 0

    async def execute(self, **kwargs) -> str:
        self.calls += 1
        return "ok"


class _DummyProvider:
    def __init__(self):
        self.last_tools = []

    def get_default_model(self) -> str:
        return "dummy-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.last_tools = tools or []
        return LLMResponse(content="done", tool_calls=[])


class _SequenceProvider:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.last_tools = []
        self.last_messages = []

    def get_default_model(self) -> str:
        return "dummy-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.calls += 1
        self.last_tools = tools or []
        self.last_messages = messages
        if not self._responses:
            return LLMResponse(content="done", tool_calls=[])
        next_item = self._responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


class _DummyContext:
    async def prepare_memory_context(self, _content: str):
        return None

    def build_messages(self, *, history, current_message, media=None, channel=None, chat_id=None):
        return [{"role": "user", "content": current_message}]

    def add_assistant_message(self, messages, content, tool_calls):
        return [*messages, {"role": "assistant", "content": content, "tool_calls": tool_calls}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return [*messages, {"role": "tool", "content": result, "tool_call_id": tool_call_id, "name": tool_name}]


class _FakeTrajectoryStore:
    def __init__(self):
        self.started = []
        self.events = []
        self.finalized = []
        self.feedback = []

    def start(self, **kwargs):
        self.started.append(kwargs)
        return "traj_test_1"

    def add_event(self, run_id, *, stage, action, payload):
        self.events.append({"run_id": run_id, "stage": stage, "action": action, "payload": payload})

    def finalize(self, run_id, *, status, final_content, usage=None, metrics=None):
        self.finalized.append(
            {
                "run_id": run_id,
                "status": status,
                "final_content": final_content,
                "usage": usage or {},
                "metrics": metrics or {},
            }
        )

    def list_recent(self, limit=50, session_key=None):
        return [{"run_id": "traj_test_1", "status": "success"}]

    def get_trajectory(self, run_id):
        if run_id != "traj_test_1":
            return None
        return {"run_id": run_id, "meta": {"session_key": "web:web-main"}, "events": [], "final": {"status": "success"}}

    def resolve_latest_run(self, *, session_key=None, chat_id=None):
        return "traj_test_1"

    def add_feedback(self, run_id, *, label, feedback, context, metadata=None):
        if run_id != "traj_test_1":
            return False
        self.feedback.append(
            {
                "run_id": run_id,
                "label": label,
                "feedback": feedback,
                "context": context,
                "metadata": metadata or {},
            }
        )
        return True

    def list_feedback_samples(self, limit=100, label=None):
        samples = [
            {
                "run_id": item["run_id"],
                "label": item["label"],
                "feedback": item["feedback"],
                "context": item["context"],
            }
            for item in self.feedback
        ]
        if label:
            label_key = str(label).strip().lower()
            samples = [item for item in samples if str(item["label"]).strip().lower() == label_key]
        return samples[:limit]


@pytest.mark.asyncio
async def test_verify_admin_token_requires_token_by_default(monkeypatch):
    monkeypatch.setattr("tools.admin.auth._is_allowed_origin", lambda _: True)
    monkeypatch.setattr("tools.admin.auth._is_loopback", lambda _: False)
    monkeypatch.setattr(admin_api, "get_owner_manager", lambda: SimpleNamespace(validate_session=lambda _: False))
    monkeypatch.setattr(admin_api, "config", SimpleNamespace(get=lambda *_args, **_kwargs: False))

    request = _make_request(host="127.0.0.1", auth="")
    with pytest.raises(HTTPException) as exc:
        await admin_api.verify_admin_token(request)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_admin_token_blocks_disallowed_origin(monkeypatch):
    monkeypatch.setattr("tools.admin.auth._is_allowed_origin", lambda origin: origin == "http://localhost:5173")
    monkeypatch.setattr("tools.admin.auth._is_loopback", lambda _: False)
    monkeypatch.setattr(admin_api, "get_owner_manager", lambda: SimpleNamespace(validate_session=lambda _t: True))
    monkeypatch.setattr(admin_api, "config", SimpleNamespace(get=lambda *_args, **_kwargs: False))

    request = _make_request(origin="http://evil.example", auth="Bearer valid-token")
    with pytest.raises(HTTPException) as exc:
        await admin_api.verify_admin_token(request)
    assert exc.value.status_code == 403


def test_default_cors_origins_allow_vite_preview_port(monkeypatch):
    assert "http://localhost:4173" in admin_auth._DEFAULT_CORS_ORIGINS
    assert "http://127.0.0.1:4173" in admin_auth._DEFAULT_CORS_ORIGINS

    monkeypatch.setattr(admin_auth, "cors_origins", list(admin_auth._DEFAULT_CORS_ORIGINS))
    monkeypatch.setattr(
        admin_auth,
        "config",
        SimpleNamespace(get=lambda key, default=None: {"api.cors_strict_mode": True}.get(key, default)),
    )

    assert admin_auth._is_allowed_origin("http://localhost:4173") is True
    assert admin_auth._is_allowed_origin("http://127.0.0.1:4173") is True


@pytest.mark.asyncio
async def test_favicon_serves_real_file(monkeypatch, tmp_path):
    icon_path = tmp_path / "favicon.ico"
    icon_path.write_bytes(b"\x00\x00\x01\x00")
    monkeypatch.setattr(admin_api, "_FAVICON_ICO_PATH", icon_path)

    response = await admin_api.favicon()
    assert response.status_code == 200
    assert Path(response.path) == icon_path
    assert response.media_type == "image/x-icon"


@pytest.mark.asyncio
async def test_favicon_returns_404_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_api, "_FAVICON_ICO_PATH", tmp_path / "missing.ico")

    with pytest.raises(HTTPException) as exc:
        await admin_api.favicon()
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_verify_admin_token_accepts_cookie_token(monkeypatch):
    monkeypatch.setattr("tools.admin.auth._is_allowed_origin", lambda _: True)
    monkeypatch.setattr("tools.admin.auth._is_loopback", lambda _: False)
    monkeypatch.setattr(
        admin_api,
        "get_owner_manager",
        lambda: SimpleNamespace(validate_session=lambda token: token == "cookie-token"),
    )
    monkeypatch.setattr(admin_api, "config", SimpleNamespace(get=lambda *_args, **_kwargs: False))

    request = _make_request(cookie="admin_token=cookie-token")
    await admin_api.verify_admin_token(request)


@pytest.mark.asyncio
async def test_verify_admin_token_rejects_bearer_when_disabled(monkeypatch):
    monkeypatch.setattr("tools.admin.auth._is_allowed_origin", lambda _: True)
    monkeypatch.setattr("tools.admin.auth._is_loopback", lambda _: False)
    monkeypatch.setattr(
        admin_api,
        "get_owner_manager",
        lambda: SimpleNamespace(
            validate_session=lambda _token, **_kwargs: False,
            validate_admin_token=lambda token: token == "valid-token",
        ),
    )
    monkeypatch.setattr(
        admin_api,
        "config",
        SimpleNamespace(
            get=lambda key, default=None: {
                "api.allow_admin_bearer_token": False,
                "api.allow_loopback_without_token": False,
            }.get(key, default)
        ),
    )

    request = _make_request(auth="Bearer valid-token")
    with pytest.raises(HTTPException) as exc:
        await admin_api.verify_admin_token(request)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_admin_token_blocks_loopback_bypass_when_proxy_headers_present(monkeypatch):
    monkeypatch.setattr("tools.admin.auth._is_allowed_origin", lambda _: True)
    monkeypatch.setattr("tools.admin.auth._is_loopback", lambda _: False)
    monkeypatch.setattr(admin_api, "get_owner_manager", lambda: SimpleNamespace(validate_session=lambda *_args, **_kwargs: False))
    monkeypatch.setattr(
        admin_api,
        "config",
        SimpleNamespace(
            get=lambda key, default=None: {
                "api.allow_loopback_without_token": True,
                "api.local_bypass_environments": ["dev", "test", "local"],
                "runtime.environment": "dev",
            }.get(key, default)
        ),
    )

    request = _make_request(
        host="127.0.0.1",
        extra_headers={"x-forwarded-for": "203.0.113.9"},
    )
    with pytest.raises(HTTPException) as exc:
        await admin_api.verify_admin_token(request)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_create_admin_session_sets_cookie(monkeypatch):
    monkeypatch.setattr("tools.admin.auth._is_allowed_origin", lambda _: True)
    monkeypatch.setattr("tools.admin.auth._is_loopback", lambda _: False)
    monkeypatch.setattr(
        admin_api,
        "get_owner_manager",
        lambda: SimpleNamespace(validate_session=lambda token: token == "valid-token"),
    )
    monkeypatch.setattr(
        admin_api,
        "config",
        SimpleNamespace(
            get=lambda key, default=None: {
                "api.cookie_secure": False,
                "api.cookie_samesite": "strict",
                "api.session_max_age_seconds": 3600,
            }.get(key, default)
        ),
    )

    request = _make_request(path="/auth/session", method="POST", auth="")
    response = await admin_api.create_admin_session({"token": "valid-token"}, request)
    set_cookie = response.headers.get("set-cookie", "")
    assert response.status_code == 200
    assert "admin_token=valid-token" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Max-Age=3600" in set_cookie
    assert "SameSite=strict" in set_cookie


@pytest.mark.asyncio
async def test_create_admin_session_uses_derived_session_token(monkeypatch):
    class _Owner:
        def validate_admin_token(self, token: str) -> bool:
            return token == "valid-token"

        def create_session(self, *, ttl_seconds: int = 3600, metadata=None) -> str:
            assert ttl_seconds == 3600
            return "sess_derived_1"

    monkeypatch.setattr("tools.admin.auth._is_allowed_origin", lambda _: True)
    monkeypatch.setattr("tools.admin.auth._is_loopback", lambda _: False)
    monkeypatch.setattr(admin_api, "get_owner_manager", lambda: _Owner())
    monkeypatch.setattr(
        admin_api,
        "config",
        SimpleNamespace(
            get=lambda key, default=None: {
                "api.cookie_secure": False,
                "api.cookie_samesite": "strict",
                "api.session_max_age_seconds": 3600,
            }.get(key, default)
        ),
    )

    request = _make_request(path="/auth/session", method="POST", auth="")
    response = await admin_api.create_admin_session({"token": "valid-token"}, request)
    set_cookie = response.headers.get("set-cookie", "")
    assert response.status_code == 200
    assert "admin_token=sess_derived_1" in set_cookie


@pytest.mark.asyncio
async def test_create_admin_session_reuses_existing_valid_session_cookie(monkeypatch):
    class _Owner:
        def __init__(self) -> None:
            self.created = 0

        def validate_admin_token(self, token: str) -> bool:
            return token == "valid-token"

        def validate_session(self, token: str, *, allow_admin_token: bool = True) -> bool:
            return (token == "sess_existing_1") and (allow_admin_token is False)

        def create_session(self, *, ttl_seconds: int = 3600, metadata=None) -> str:
            self.created += 1
            return f"sess_new_{self.created}"

    owner = _Owner()
    monkeypatch.setattr("tools.admin.auth._is_allowed_origin", lambda _: True)
    monkeypatch.setattr("tools.admin.auth._is_loopback", lambda _: False)
    monkeypatch.setattr(admin_api, "get_owner_manager", lambda: owner)
    monkeypatch.setattr(
        admin_api,
        "config",
        SimpleNamespace(
            get=lambda key, default=None: {
                "api.cookie_secure": False,
                "api.cookie_samesite": "strict",
                "api.session_max_age_seconds": 3600,
            }.get(key, default)
        ),
    )

    request = _make_request(
        path="/auth/session",
        method="POST",
        auth="",
        cookie="admin_token=sess_existing_1",
    )
    response = await admin_api.create_admin_session({"token": "valid-token"}, request)
    set_cookie = response.headers.get("set-cookie", "")
    assert response.status_code == 200
    assert owner.created == 0
    assert "admin_token=sess_existing_1" in set_cookie


@pytest.mark.asyncio
async def test_clear_admin_session_deletes_cookie(monkeypatch):
    monkeypatch.setattr("tools.admin.auth._is_allowed_origin", lambda _: True)
    monkeypatch.setattr("tools.admin.auth._is_loopback", lambda _: False)
    monkeypatch.setattr(
        admin_api,
        "config",
        SimpleNamespace(
            get=lambda key, default=None: {
                "api.cookie_secure": False,
                "api.cookie_samesite": "strict",
            }.get(key, default)
        ),
    )

    request = _make_request(path="/auth/session", method="DELETE")
    response = await admin_api.clear_admin_session(request)
    set_cookie = response.headers.get("set-cookie", "")
    assert response.status_code == 200
    assert "admin_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Max-Age=0" in set_cookie


@pytest.mark.asyncio
async def test_export_path_rejects_outside_allowed_dirs(monkeypatch):
    fake_cfg = _FakeConfig({})
    monkeypatch.setattr(admin_api, "config", fake_cfg)
    outside = (Path(admin_api._PROJECT_ROOT).parent / "outside_report.md").resolve()
    with pytest.raises(HTTPException) as exc:
        await admin_api.export_flowise_roundtrip_report({"output_path": str(outside)})
    assert exc.value.status_code == 400
    assert "allowed export dirs" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_export_path_rejects_protected_config_file(monkeypatch):
    fake_cfg = _FakeConfig({"api": {"export_allowed_dirs": ["config"]}})
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    with pytest.raises(HTTPException) as exc:
        await admin_api.export_flowise_roundtrip_report({"output_path": "config/settings.yaml"})
    assert exc.value.status_code == 400
    assert "protected config file" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_clear_policy_audit_blocked_by_default(monkeypatch):
    monkeypatch.setattr(
        admin_api,
        "config",
        SimpleNamespace(get=lambda key, default=None: {"api.allow_audit_buffer_clear": False}.get(key, default)),
    )
    with pytest.raises(HTTPException) as exc:
        await admin_api.clear_policy_audit()
    assert exc.value.status_code == 403
    assert "Audit clear is disabled" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_config_blocks_nested_protected_keys(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "security": {
                "dm_policy": "pairing",
                "auto_approve_privileged": False,
                "owner_channel_ids": {},
            }
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    with pytest.raises(HTTPException) as exc:
        await admin_api.update_config({"security": {"dm_policy": "open"}})
    assert exc.value.status_code == 403
    assert fake_cfg.set_many_calls == []


@pytest.mark.asyncio
async def test_update_config_blocks_loopback_auth_bypass_toggle(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "api": {"allow_loopback_without_token": False},
            "security": {
                "dm_policy": "pairing",
                "auto_approve_privileged": False,
                "owner_channel_ids": {},
                "tool_groups": {},
            },
            "agents": {"list": []},
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    with pytest.raises(HTTPException) as exc:
        await admin_api.update_config({"api": {"allow_loopback_without_token": True}})
    assert exc.value.status_code == 403
    assert fake_cfg.set_many_calls == []


@pytest.mark.asyncio
async def test_update_config_blocks_internal_planning_namespace(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "agents": {
                "defaults": {
                    "planning": {
                        "mode": "auto",
                        "auto": {
                            "min_message_chars": 220,
                        },
                    }
                }
            }
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    with pytest.raises(HTTPException) as exc:
        await admin_api.update_config({"agents": {"defaults": {"planning": {"mode": "always"}}}})
    assert exc.value.status_code == 403
    assert fake_cfg.set_many_calls == []


@pytest.mark.asyncio
async def test_update_config_uses_single_batch_write(monkeypatch):
    fake_cfg = _FakeConfig({"personality": {"name": "Gazer"}, "voice": {"provider": "edge-tts"}})
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    result = await admin_api.update_config({"personality": {"name": "Neo"}, "voice": {"provider": "edge-tts"}})
    assert result["status"] == "success"
    assert len(fake_cfg.set_many_calls) == 1
    assert fake_cfg.set_many_calls[0] == {"personality.name": "Neo"}


@pytest.mark.asyncio
async def test_update_config_allows_owner_channel_ids(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "security": {
                "dm_policy": "pairing",
                "auto_approve_privileged": False,
                "owner_channel_ids": {},
            }
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    result = await admin_api.update_config({"security": {"owner_channel_ids": {"feishu": "ou_123"}}})
    assert result["status"] == "success"
    assert len(fake_cfg.set_many_calls) == 1
    assert fake_cfg.set_many_calls[0]["security.owner_channel_ids"] == {"feishu": "ou_123"}


@pytest.mark.asyncio
async def test_update_config_replaces_owner_channel_ids_map(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "security": {
                "dm_policy": "pairing",
                "auto_approve_privileged": False,
                "owner_channel_ids": {
                    "feishu": "ou_keep",
                    "telegram": "1001",
                },
            }
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    result = await admin_api.update_config({"security": {"owner_channel_ids": {"feishu": "ou_keep"}}})
    assert result["status"] == "success"
    assert len(fake_cfg.set_many_calls) == 1
    assert fake_cfg.set_many_calls[0] == {"security.owner_channel_ids": {"feishu": "ou_keep"}}


def test_run_verify_command_blocks_shell_metacharacters():
    result = admin_api._run_verify_command("pytest -q && whoami", Path("."), timeout_seconds=30)
    assert result["ok"] is False
    assert result["returncode"] == -1
    assert "blocked shell metacharacters" in result["stderr"]


def test_run_verify_command_blocks_shell_executables():
    result = admin_api._run_verify_command("powershell.exe -Command whoami", Path("."), timeout_seconds=30)
    assert result["ok"] is False
    assert result["returncode"] == -1
    assert "blocked verify executable" in result["stderr"]


def test_run_verify_command_executes_without_shell(monkeypatch):
    called = {}

    class _Proc:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        called["cmd"] = cmd
        called["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(admin_api._subprocess, "run", _fake_run)
    result = admin_api._run_verify_command("pytest -q", Path("."), timeout_seconds=30)

    assert result["ok"] is True
    assert called["cmd"] == ["pytest", "-q"]
    assert called["kwargs"]["shell"] is False



@pytest.mark.asyncio
async def test_skill_update_rejects_path_traversal(monkeypatch, tmp_path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n", encoding="utf-8")

    monkeypatch.setattr(admin_api, "_resolve_skill_dir", lambda _name: str(skill_dir))

    with pytest.raises(HTTPException) as exc:
        await admin_api.update_skill_content("demo", {"file": "../outside.txt", "content": "pwnd"})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_chat_connection_manager_isolates_sessions():
    manager = admin_api.ChatConnectionManager()
    ws_a = _DummyWs()
    ws_b = _DummyWs()

    await manager.connect(ws_a, "chat-a")
    await manager.connect(ws_b, "chat-b")

    await manager.broadcast("chat-a", {"type": "chat_stream", "chat_id": "chat-a", "content": "hello a"})
    await manager.broadcast("chat-b", {"type": "chat_stream", "chat_id": "chat-b", "content": "hello b"})

    assert ws_a.messages == [{"type": "chat_stream", "chat_id": "chat-a", "content": "hello a"}]
    assert ws_b.messages == [{"type": "chat_stream", "chat_id": "chat-b", "content": "hello b"}]


@pytest.mark.asyncio
async def test_exec_tool_uses_native_backend(tmp_path):
    class _Shell:
        def __init__(self):
            self.calls = []

        async def exec(self, command: str, cwd: str, *, timeout: int = 30):
            self.calls.append((command, cwd, timeout))
            return 0, "sandbox-ok\n", ""

    shell = _Shell()
    tool = ExecTool(Path(tmp_path), shell_ops=shell)
    result = await tool.execute(command="echo hi")

    assert "hi" in result.lower()
    assert len(shell.calls) == 0






@pytest.mark.asyncio
async def test_agent_loop_applies_group_policy_to_definitions(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_groups": {
                "coding": ["read_file", "write_file"],
            },
        }
    }
    fake_config = _FakeConfig(fake_data)
    monkeypatch.setattr(config_manager, "config", fake_config)
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    provider = _DummyProvider()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
        tool_policy={"allow_groups": ["coding"]},
    )
    loop.tools.register(_DummyTool("read_file", False, provider="coding"))
    loop.tools.register(_DummyTool("write_file", False, provider="coding"))
    loop.tools.register(_DummyTool("web_search", False, provider="web"))

    msg = InboundMessage(channel="web", sender_id="WebUser", chat_id="web-main", content="please help")
    out = await loop._process_message(msg)

    assert out is not None
    exposed = [tool_def["function"]["name"] for tool_def in provider.last_tools]
    assert sorted(exposed) == ["read_file", "write_file"]


@pytest.mark.asyncio
async def test_agent_loop_records_basic_trajectory(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_groups": {},
            "llm_max_retries": 0,
            "llm_retry_backoff_seconds": 0.0,
        }
    }
    monkeypatch.setattr(config_manager, "config", _FakeConfig(fake_data))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    provider = _SequenceProvider([LLMResponse(content="final answer", tool_calls=[])])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    fake_store = _FakeTrajectoryStore()
    loop.trajectory_store = fake_store

    msg = InboundMessage(channel="web", sender_id="WebUser", chat_id="web-main", content="hello")
    out = await loop._process_message(msg)

    assert out is not None
    assert out.content == "final answer"
    assert len(fake_store.started) == 1
    assert any(event["action"] == "llm_request" for event in fake_store.events)
    assert any(event["action"] == "llm_response" for event in fake_store.events)
    assert len(fake_store.finalized) == 1
    assert fake_store.finalized[0]["status"] == "success"


@pytest.mark.asyncio
async def test_agent_loop_normalizes_string_tool_arguments(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_call_timeout_seconds": 2.0,
            "tool_groups": {},
        }
    }
    monkeypatch.setattr(config_manager, "config", _FakeConfig(fake_data))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    class _EchoTool(_DummyTool):
        async def execute(self, **kwargs) -> str:
            return f"echo:{kwargs.get('text', '')}"

    provider = _SequenceProvider(
        [
            LLMResponse(
                content="calling tool",
                tool_calls=[
                    ToolCallRequest(
                        id="tc1",
                        name="echo_tool",
                        arguments='{"text":"hello"}',
                    )
                ],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    loop.tools.register(_EchoTool("echo_tool", False))

    msg = InboundMessage(channel="web", sender_id="WebUser", chat_id="web-main", content="please help")
    out = await loop._process_message(msg)

    assert out is not None
    assert out.content == "done"
    tool_messages = [m for m in provider.last_messages if m.get("role") == "tool"]
    assert any("echo:hello" in str(m.get("content")) for m in tool_messages)


@pytest.mark.asyncio
async def test_agent_loop_non_owner_cannot_execute_privileged_tool(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_call_timeout_seconds": 2.0,
            "tool_groups": {},
            "llm_max_retries": 0,
            "llm_retry_backoff_seconds": 0.0,
        }
    }
    monkeypatch.setattr(config_manager, "config", _FakeConfig(fake_data))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    class _PrivilegedNoopTool(_DummyTool):
        def __init__(self, name: str):
            super().__init__(name, True)
            self.calls = 0

        async def execute(self, **kwargs) -> str:
            self.calls += 1
            return "should-not-run"

    provider = _SequenceProvider(
        [
            LLMResponse(
                content="try tool",
                tool_calls=[ToolCallRequest(id="tc1", name="priv_noop", arguments={})],
            ),
            LLMResponse(content="final answer", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    tool = _PrivilegedNoopTool("priv_noop")
    loop.tools.register(tool)

    first = await loop._process_message(
        InboundMessage(channel="web", sender_id="WebUser", chat_id="chat1", content="run")
    )
    assert first is not None
    assert first.content == "final answer"
    assert tool.calls == 0
    assert provider.calls == 2
    tool_messages = [m for m in provider.last_messages if m.get("role") == "tool"]
    assert any("TOOL_NOT_PERMITTED" in str(m.get("content")) for m in tool_messages)


@pytest.mark.asyncio
async def test_agent_loop_parallel_tool_errors_do_not_break_response(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_call_timeout_seconds": 2.0,
            "tool_groups": {},
        }
    }
    monkeypatch.setattr(config_manager, "config", _FakeConfig(fake_data))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    class _OkTool(_DummyTool):
        async def execute(self, **kwargs) -> str:
            return "ok-tool"

    provider = _SequenceProvider(
        [
            LLMResponse(
                content="parallel tools",
                tool_calls=[
                    ToolCallRequest(id="tc1", name="ok_tool", arguments={}),
                    ToolCallRequest(id="tc2", name="bad_args_tool", arguments='["not_object"]'),
                ],
            ),
            LLMResponse(content="final answer", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    loop.tools.register(_OkTool("ok_tool", False))
    loop.tools.register(_OkTool("bad_args_tool", False))

    msg = InboundMessage(channel="web", sender_id="WebUser", chat_id="web-main", content="please help")
    out = await loop._process_message(msg)

    assert out is not None
    assert out.content == "final answer"
    tool_messages = [m for m in provider.last_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert any("Invalid parameters" in str(m.get("content")) for m in tool_messages)


@pytest.mark.asyncio
async def test_agent_loop_tool_timeout_returns_error_and_recovers(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_call_timeout_seconds": 0.01,
            "tool_groups": {},
        }
    }
    monkeypatch.setattr(config_manager, "config", _FakeConfig(fake_data))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    class _SlowTool(_DummyTool):
        async def execute(self, **kwargs) -> str:
            await asyncio.sleep(0.05)
            return "slow-ok"

    provider = _SequenceProvider(
        [
            LLMResponse(
                content="run slow tool",
                tool_calls=[ToolCallRequest(id="tc1", name="slow_tool", arguments={})],
            ),
            LLMResponse(content="final answer", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    loop.tools.register(_SlowTool("slow_tool", False))

    msg = InboundMessage(channel="web", sender_id="WebUser", chat_id="web-main", content="please help")
    out = await loop._process_message(msg)

    assert out is not None
    assert out.content == "final answer"
    tool_messages = [m for m in provider.last_messages if m.get("role") == "tool"]
    assert any("timed out" in str(m.get("content")) for m in tool_messages)


@pytest.mark.asyncio
async def test_agent_loop_retries_llm_exception_and_recovers(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_groups": {},
            "llm_max_retries": 1,
            "llm_retry_backoff_seconds": 0.0,
        }
    }
    monkeypatch.setattr(config_manager, "config", _FakeConfig(fake_data))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    provider = _SequenceProvider(
        [
            RuntimeError("transient network error"),
            LLMResponse(content="final answer", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )

    msg = InboundMessage(channel="web", sender_id="WebUser", chat_id="web-main", content="hello")
    out = await loop._process_message(msg)

    assert out is not None
    assert out.content == "final answer"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_agent_loop_returns_readable_message_when_llm_retries_exhausted(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_groups": {},
            "llm_max_retries": 1,
            "llm_retry_backoff_seconds": 0.0,
        }
    }
    monkeypatch.setattr(config_manager, "config", _FakeConfig(fake_data))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    provider = _SequenceProvider(
        [
            RuntimeError("network down"),
            RuntimeError("network down"),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )

    msg = InboundMessage(channel="web", sender_id="WebUser", chat_id="web-main", content="hello")
    out = await loop._process_message(msg)

    assert out is not None
    assert "couldn't get a valid model response" in out.content
    assert "failed after 2 attempts" in out.content
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_agent_loop_llm_retry_budget_exhausted(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_groups": {},
            "llm_max_retries": 3,
            "llm_retry_backoff_seconds": 0.0,
            "retry_budget_total": 0,
        }
    }
    monkeypatch.setattr(config_manager, "config", _FakeConfig(fake_data))
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        lambda: SimpleNamespace(is_owner_sender=lambda *_args, **_kwargs: False),
    )

    provider = _SequenceProvider(
        [
            RuntimeError("temporary network error"),
            LLMResponse(content="should-not-be-used", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )

    msg = InboundMessage(channel="web", sender_id="WebUser", chat_id="web-main", content="hello")
    out = await loop._process_message(msg)

    assert out is not None
    assert "Retry budget exhausted" in out.content
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_agent_loop_release_gate_blocks_standard_tools(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_groups": {},
            "release_gate_enforcement": True,
            "release_gate_owner_bypass": False,
        }
    }
    monkeypatch.setattr(config_manager, "config", _FakeConfig(fake_data))

    provider = _SequenceProvider(
        [
            LLMResponse(
                content="calling tool",
                tool_calls=[ToolCallRequest(id="tc1", name="std_tool", arguments={})],
            ),
            LLMResponse(content="final answer", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    std_tool = _CountingTool("std_tool", False, provider="coding")
    loop.tools.register(std_tool)
    loop._eval_benchmark_manager = SimpleNamespace(  # type: ignore[attr-defined]
        get_release_gate_status=lambda: {"blocked": True, "reason": "quality_gate_blocked"}
    )

    msg = InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="run it")
    out = await loop._process_message(msg)

    assert out is not None
    assert out.content == "final answer"
    assert std_tool.calls == 0


@pytest.mark.asyncio
async def test_agent_loop_release_gate_allows_safe_tools(monkeypatch, tmp_path):
    fake_data = {
        "security": {

            "tool_groups": {},
            "release_gate_enforcement": True,
            "release_gate_owner_bypass": False,
        }
    }
    monkeypatch.setattr(config_manager, "config", _FakeConfig(fake_data))

    provider = _SequenceProvider(
        [
            LLMResponse(
                content="calling safe tool",
                tool_calls=[ToolCallRequest(id="tc1", name="safe_tool", arguments={})],
            ),
            LLMResponse(content="final answer", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    safe_tool = _CountingTool("safe_tool", False, provider="coding", bypass_release_gate=True)
    loop.tools.register(safe_tool)
    loop._eval_benchmark_manager = SimpleNamespace(  # type: ignore[attr-defined]
        get_release_gate_status=lambda: {"blocked": True, "reason": "quality_gate_blocked"}
    )

    msg = InboundMessage(channel="web", sender_id="u1", chat_id="chat1", content="run it")
    out = await loop._process_message(msg)

    assert out is not None
    assert out.content == "final answer"
    assert safe_tool.calls == 1


@pytest.mark.asyncio
async def test_policy_explain_endpoint_reports_reason(monkeypatch):
    registry = ToolRegistry()
    registry.register(_DummyTool("priv_tool", True, provider="desktop"))
    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", registry)
    monkeypatch.setattr(
        admin_api,
        "config",
        SimpleNamespace(get=lambda key, default=None: {"security.tool_groups": {}}.get(key, default)),
    )

    res = await admin_api.explain_policy({"tool_name": "priv_tool", "max_tier": "safe"})
    assert res["status"] == "ok"
    assert res["result"]["allowed"] is False
    assert res["result"]["reason"] == "blocked_by_owner_only_no_context"


@pytest.mark.asyncio
async def test_policy_simulate_endpoint_with_request_policy(monkeypatch):
    registry = ToolRegistry()
    registry.register(_DummyTool("read_file", False, provider="coding"))
    registry.register(_DummyTool("web_search", False, provider="web"))
    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", registry)
    fake_cfg = _FakeConfig({"security": {"tool_groups": {"coding": ["read_file"]}}})
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    res = await admin_api.simulate_policy(
        {"policy": {"allow_groups": ["coding"]}, "max_tier": "privileged"}
    )
    assert res["status"] == "ok"
    by_name = {item["tool"]: item for item in res["results"]}
    assert by_name["read_file"]["allowed"] is True
    assert by_name["web_search"]["allowed"] is False


@pytest.mark.asyncio
async def test_policy_explain_endpoint_reports_layer_conflicts(monkeypatch, tmp_path: Path):
    registry = ToolRegistry()
    registry.register(_DummyTool("web_search", False, provider="web"))
    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", registry)

    workspace = tmp_path / "workspace"
    (workspace / "apps").mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("allowed-tools: web_search\n", encoding="utf-8")
    (workspace / "apps" / "AGENTS.md").write_text("deny-tools: web_search\n", encoding="utf-8")
    monkeypatch.setattr(admin_api, "_PROJECT_ROOT", workspace)

    fake_cfg = _FakeConfig(
        {
            "security": {
                "tool_groups": {},
                "tool_allowlist": [],
                "tool_denylist": ["web_search"],
                "tool_allow_providers": [],
                "tool_deny_providers": [],
            },
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    res = await admin_api.explain_policy(
        {
            "tool_name": "web_search",
            "policy": {"allow_names": ["web_search"]},
            "agents_target_dir": "apps",
            "max_tier": "privileged",
        }
    )
    assert res["status"] == "ok"
    assert res["result"]["allowed"] is False
    assert res["result"]["reason"] == "blocked_by_policy_deny_names"
    explain = res["explain"]
    assert explain["layers"]["directory"]["target_dir"] == "apps"
    assert "web_search" in explain["layers"]["effective"]["deny_names"]
    assert any(
        item.get("type") == "allow_deny_conflict"
        and item.get("value", item.get("tool")) == "web_search"
        for item in explain["conflicts"]
    )


@pytest.mark.asyncio
async def test_policy_explain_endpoint_with_model_context(monkeypatch):
    registry = ToolRegistry()
    registry.register(_DummyTool("web_search", False, provider="web"))
    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", registry)
    fake_cfg = _FakeConfig({"security": {"tool_groups": {}}, "agents": {"list": []}})
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    res = await admin_api.explain_policy(
        {
            "tool_name": "web_search",
            "max_tier": "privileged",
            "policy": {"allow_model_selectors": ["openai/gpt-4o-mini"]},
            "model_provider": "openai",
            "model_name": "gpt-4o",
        }
    )

    assert res["status"] == "ok"
    assert res["result"]["allowed"] is False
    assert res["result"]["reason"] == "blocked_by_policy_allow_model_selectors"
    assert res["explain"]["model_context"]["available"] is True
    assert any(item.get("rule") == "policy_allow_model_selectors" for item in res["explain"]["rule_chain"])


@pytest.mark.asyncio
async def test_policy_effective_endpoint_includes_directory_conflicts(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "apps").mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("allowed-tools: web_search\n", encoding="utf-8")
    (workspace / "apps" / "AGENTS.md").write_text("deny-tools: web_search\n", encoding="utf-8")
    monkeypatch.setattr(admin_api, "_PROJECT_ROOT", workspace)

    fake_cfg = _FakeConfig(
        {
            "security": {

                "tool_allowlist": [],
                "tool_denylist": ["web_search"],
                "tool_allow_providers": [],
                "tool_deny_providers": [],
                "tool_groups": {},
            },
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    result = await admin_api.get_effective_policy(agents_target_dir="apps")
    assert result["status"] == "ok"
    assert result["directory"]["target_dir"] == "apps"
    assert "web_search" in result["directory"]["policy"]["deny_names"]
    assert any(
        item.get("type") == "allow_deny_conflict"
        and item.get("value", item.get("tool")) == "web_search"
        for item in result["conflicts"]
    )


@pytest.mark.asyncio
async def test_policy_effective_endpoint_preview_with_model_context(monkeypatch):
    registry = ToolRegistry()
    registry.register(_DummyTool("web_search", False, provider="web"))
    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", registry)

    fake_cfg = _FakeConfig(
        {
            "security": {
                "tool_allowlist": [],
                "tool_denylist": [],
                "tool_allow_providers": [],
                "tool_deny_providers": [],
                "tool_groups": {},
                "tool_policy_v3": {"allow_model_names": ["gpt-4o-mini"]},
            },
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    result = await admin_api.get_effective_policy(
        tool_name="web_search",
        model_provider="openai",
        model_name="gpt-4o",
    )

    assert result["status"] == "ok"
    assert result["preview"]["decision"]["allowed"] is False
    assert result["preview"]["decision"]["reason"] == "blocked_by_policy_allow_model_names"





@pytest.mark.asyncio
async def test_debug_trajectory_endpoints(monkeypatch):
    store = _FakeTrajectoryStore()
    monkeypatch.setattr(admin_api, "TRAJECTORY_STORE", store)

    listing = await admin_api.list_trajectories(limit=10)
    assert listing["total"] == 1
    assert listing["items"][0]["run_id"] == "traj_test_1"

    payload = await admin_api.get_trajectory("traj_test_1")
    assert payload["run_id"] == "traj_test_1"

    with pytest.raises(HTTPException) as exc:
        await admin_api.get_trajectory("missing")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_feedback_attaches_to_trajectory_and_exports_eval_samples(monkeypatch):
    store = _FakeTrajectoryStore()
    monkeypatch.setattr(admin_api, "TRAJECTORY_STORE", store)

    class _Evolution:
        def __init__(self):
            self.calls = []

        def collect_feedback(self, label, context, feedback):
            self.calls.append((label, context, feedback))

        async def maybe_auto_optimize(self, trigger="feedback"):
            return {
                "enabled": False,
                "attempted": False,
                "updated": False,
                "reason": "disabled",
                "trigger": trigger,
            }

    evo = _Evolution()
    monkeypatch.setattr(admin_api, "get_evolution", lambda: evo)

    response = await admin_api.submit_feedback(
        {
            "label": "positive",
            "feedback": "worked well",
            "context": "chat_ui",
            "session_key": "web:web-main",
        }
    )
    assert response["status"] == "feedback_received"
    assert response["attached_to_trajectory"] is True
    assert response["run_id"] == "traj_test_1"
    assert response["auto_optimize"]["reason"] == "disabled"
    assert len(store.feedback) == 1
    assert len(evo.calls) == 1

    export_res = await admin_api.get_eval_samples(limit=10, label="positive")
    assert export_res["total"] == 1
    assert export_res["samples"][0]["run_id"] == "traj_test_1"
    assert export_res["samples"][0]["label"] == "positive"


@pytest.mark.asyncio
async def test_evolution_history_endpoints(monkeypatch):
    class _Evolution:
        def get_recent_history(self, limit=50):
            return [
                {"event": "auto_optimize", "reason": "disabled", "timestamp": "t1"},
                {"event": "optimize_persona", "reason": "updated", "timestamp": "t2"},
            ][:limit]

        def get_history_summary(self):
            return {"total": 1, "updated": 0, "not_updated": 1, "by_event": {"auto_optimize": 1}, "by_reason": {"disabled": 1}}

        def clear_history(self):
            return 1

    monkeypatch.setattr(admin_api, "get_evolution", lambda: _Evolution())
    listing = await admin_api.get_evolution_history(limit=10)
    assert listing["status"] == "ok"
    assert listing["total"] == 2
    filtered = await admin_api.get_evolution_history(limit=10, event="auto_optimize")
    assert filtered["total"] == 1
    csv_payload = await admin_api.get_evolution_history(limit=10, format="csv")
    assert "text/csv" in csv_payload.media_type
    summary = await admin_api.get_evolution_history_summary()
    assert summary["status"] == "ok"
    assert summary["summary"]["total"] == 1
    cleared = await admin_api.clear_evolution_history()
    assert cleared["status"] == "ok"
    assert cleared["cleared"] == 1


@pytest.mark.asyncio
async def test_debug_hardware_status_endpoint(monkeypatch):
    class _Ear:
        def get_status(self):
            return {"ok": True, "device_count": 2}

    monkeypatch.setattr(admin_api, "config", _FakeConfig({"perception": {"camera_enabled": True, "camera_device_index": 0}, "asr": {"input_device": None}, "body": {"type": "none"}}))
    monkeypatch.setattr("perception.ear.get_ear", lambda: _Ear())
    payload = await admin_api.get_hardware_status()
    assert payload["status"] == "ok"
    assert "hardware" in payload
    assert "microphone" in payload["hardware"]


@pytest.mark.asyncio
async def test_eval_benchmark_api_endpoints(monkeypatch, tmp_path):
    store = _FakeTrajectoryStore()
    store.feedback.append(
        {
            "run_id": "traj_test_1",
            "label": "positive",
            "feedback": "good",
            "context": "chat",
            "metadata": {},
        }
    )
    monkeypatch.setattr(admin_api, "TRAJECTORY_STORE", store)
    manager = EvalBenchmarkManager(base_dir=tmp_path / "eval")
    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: manager)

    build_res = await admin_api.build_eval_benchmark({"name": "main_regression", "limit": 50})
    assert build_res["status"] == "ok"
    dataset_id = build_res["dataset"]["id"]
    assert build_res["dataset"]["sample_count"] == 1

    listing = await admin_api.list_eval_benchmarks(limit=10)
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == dataset_id

    detail = await admin_api.get_eval_benchmark(dataset_id)
    assert detail["id"] == dataset_id

    run_res = await admin_api.run_eval_benchmark(dataset_id, {"outputs": {"traj_test_1": "improved answer"}})
    assert run_res["status"] == "ok"
    assert run_res["report"]["sample_count"] == 1
    assert "composite_score" in run_res["report"]
    assert "quality_gate" in run_res["report"]
    assert "release_gate" in run_res
    assert "optimization" in run_res

    run_res2 = await admin_api.run_eval_benchmark(
        dataset_id,
        {
            "outputs": {"traj_test_1": "fallback error"},
            "gate": {"min_composite_score": 0.99, "min_pass_rate": 0.99, "max_error_rate": 0.0},
        },
    )
    assert run_res2["status"] == "ok"

    run_res3 = await admin_api.run_eval_benchmark(
        dataset_id,
        {
            "outputs": {"traj_test_1": "fallback error"},
            "gate": {"min_composite_score": 0.99, "min_pass_rate": 0.99, "max_error_rate": 0.0},
        },
    )
    assert run_res3["status"] == "ok"
    assert run_res3["optimization"]["task_created"] is True
    assert run_res3["training_job"] is not None
    assert run_res3["training_job"]["status"] == "completed"
    assert "prompt_patch" in (run_res3["training_job"].get("output") or {})

    training_jobs = await admin_api.list_training_jobs(limit=10)
    assert training_jobs["status"] == "ok"
    assert training_jobs["total"] >= 1
    training_job_id = training_jobs["items"][0]["job_id"]

    training_detail = await admin_api.get_training_job(training_job_id)
    assert training_detail["status"] == "ok"
    assert training_detail["job"]["job_id"] == training_job_id

    opt_tasks = await admin_api.list_optimization_tasks(limit=10, status="open", dataset_id=dataset_id)
    assert opt_tasks["status"] == "ok"
    assert opt_tasks["total"] == 1
    task_id = opt_tasks["items"][0]["task_id"]

    updated_task = await admin_api.update_optimization_task_status(
        task_id,
        {"status": "resolved", "note": "fixed"},
    )
    assert updated_task["status"] == "ok"
    assert updated_task["task"]["status"] == "resolved"

    runs = await admin_api.list_eval_benchmark_runs(dataset_id, limit=10)
    assert runs["total"] == 3
    assert len(runs["items"]) == 3

    latest = await admin_api.get_latest_eval_benchmark_run(dataset_id)
    assert latest["dataset_id"] == dataset_id

    compare = await admin_api.compare_eval_benchmark_runs(dataset_id, baseline_index=1)
    assert compare["dataset_id"] == dataset_id
    assert "delta" in compare

    gate = await admin_api.evaluate_eval_benchmark_gate(
        dataset_id,
        {
            "gate": {
                "min_composite_score": 0.99,
                "min_pass_rate": 0.99,
                "max_error_rate": 0.0,
            },
            "run_index": 0,
        },
    )
    assert gate["status"] == "ok"
    assert "gate" in gate["evaluation"]

    current_gate = await admin_api.get_release_gate_status()
    assert current_gate["status"] == "ok"
    assert "gate" in current_gate

    manual = await admin_api.override_release_gate(
        {"blocked": False, "reason": "manual unblock", "source": "test_suite"}
    )
    assert manual["status"] == "ok"
    assert manual["gate"]["blocked"] is False





@pytest.mark.asyncio
async def test_observability_metrics_endpoint(monkeypatch):
    class _Router:
        def get_status(self):
            return {
                "strategy": "priority",
                "budget": {"enabled": True, "used_calls": 10, "max_calls": 100},
                "providers": [
                    {
                        "name": "openai",
                        "model": "gpt-4o",
                        "calls": 10,
                        "failures": 2,
                        "p95_latency_ms": 1200.0,
                        "error_classes": {"timeout": 1, "rate_limit": 1},
                    }
                ],
            }

    class _Store:
        def list_recent(self, limit=200):
            return [
                {"status": "success", "final_preview": "ok", "turn_latency_ms": 800.0},
                {"status": "error", "final_preview": "timeout happened", "turn_latency_ms": 2200.0},
            ]

    class _Registry:
        def get_budget_runtime_status(self):
            return {
                "enabled": True,
                "window_seconds": 60,
                "max_calls": 10,
                "used_calls": 2,
                "remaining_calls": 8,
                "max_weight": 10.0,
                "used_weight": 2.0,
                "remaining_weight": 8.0,
                "group_caps": {"system": 5},
                "group_usage": {"system": {"used_calls": 2, "cap_calls": 5, "remaining_calls": 3}},
            }

        def get_recent_rejection_events(self, limit=20):
            return [
                {
                    "code": "TOOL_BUDGET_EXCEEDED",
                    "tool": "safe_tool",
                    "provider": "system",
                    "reason": "max_calls",
                }
            ][:limit]

    monkeypatch.setattr("tools.admin.observability.get_llm_router", lambda: _Router())
    monkeypatch.setattr("tools.admin.observability.get_trajectory_store", lambda: _Store())
    monkeypatch.setattr("tools.admin.observability.get_tool_registry", lambda: _Registry())
    monkeypatch.setattr("tools.admin.observability.TOOL_REGISTRY", _Registry())
    monkeypatch.setattr("tools.admin._shared.TOOL_REGISTRY", _Registry())
    monkeypatch.setattr("tools.admin.strategy_helpers._state.TOOL_REGISTRY", _Registry())
    monkeypatch.setattr(
        "tools.admin.system._build_training_bridge_policy_scoreboard",
        lambda limit=50, dataset_id=None: {
            "generated_at": 0.0,
            "total_datasets": 0,
            "datasets": [],
            "global": {"avg_policy_score": None, "best_dataset": None, "worst_dataset": None},
        },
    )

    payload = await admin_api.get_observability_metrics(limit=20)
    assert payload["status"] == "ok"
    assert payload["provider"]["total_calls"] == 10
    assert payload["provider"]["total_failures"] == 2
    assert len(payload["model"]) >= 1
    assert len(payload["agent"]) >= 1
    assert payload["tool_governance"]["budget"]["used_calls"] == 2
    assert payload["tool_governance"]["recent_rejections"][0]["code"] == "TOOL_BUDGET_EXCEEDED"
    assert "failure_attribution" in payload
    assert "by_label" in payload["failure_attribution"]
    assert payload["policy_scoreboard"]["total_datasets"] == 0


@pytest.mark.asyncio
async def test_tool_governance_health_endpoint(monkeypatch):
    class _Registry:
        def get_budget_runtime_status(self):
            return {"enabled": False, "used_calls": 0, "max_calls": 120}

        def get_recent_rejection_events(self, limit=50):
            return [{"code": "TOOL_NOT_PERMITTED", "tool": "priv_tool"}][:limit]

    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", _Registry())
    monkeypatch.setattr("tools.admin.strategy_helpers._state.TOOL_REGISTRY", _Registry())

    payload = await admin_api.get_tool_governance_health(limit=5)
    assert payload["status"] == "ok"
    assert payload["tool_governance"]["available"] is True
    assert payload["tool_governance"]["budget"]["enabled"] is False
    assert payload["tool_governance"]["recent_rejections"][0]["code"] == "TOOL_NOT_PERMITTED"


@pytest.mark.asyncio
async def test_tool_governance_slo_endpoint_and_export(monkeypatch, tmp_path: Path):
    class _Store:
        def list_recent(self, limit=200):
            return [{"run_id": "r1"}, {"run_id": "r2"}][:limit]

        def get_trajectory(self, run_id):
            if run_id == "r1":
                return {
                    "events": [
                        {"ts": 10.0, "action": "tool_call", "payload": {"tool": "echo", "tool_call_id": "tc1"}},
                        {"ts": 10.5, "action": "tool_result", "payload": {"tool": "echo", "tool_call_id": "tc1", "status": "ok"}},
                        {"ts": 20.0, "action": "tool_call", "payload": {"tool": "echo", "tool_call_id": "tc2"}},
                        {"ts": 22.0, "action": "tool_result", "payload": {"tool": "echo", "tool_call_id": "tc2", "status": "error", "error_code": "TOOL_EXECUTION_FAILED"}},
                    ]
                }
            if run_id == "r2":
                return {
                    "events": [
                        {"ts": 30.0, "action": "tool_call", "payload": {"tool": "echo", "tool_call_id": "tc3"}},
                        {"ts": 31.0, "action": "tool_result", "payload": {"tool": "echo", "tool_call_id": "tc3", "status": "ok"}},
                    ]
                }
            return None

    class _Registry:
        def get_recent_rejection_events(self, limit=50):
            events = [
                {"ts": 21.0, "code": "TOOL_BUDGET_EXCEEDED", "tool": "echo", "provider": "core", "reason": "max_calls"},
                {"ts": 25.0, "code": "TOOL_CIRCUIT_OPEN", "tool": "echo", "provider": "core", "reason": "circuit_open"},
            ]
            return events[:limit]

    monkeypatch.setattr(
        "tools.admin.observability._build_llm_tool_failure_profile",
        lambda limit=200: {"tool": {"calls": 3, "failures": 1, "success_rate": 0.6667}}
    )
    monkeypatch.setattr(
        "tools.admin.observability._build_tool_timing_profile",
        lambda limit=200: {"p95_latency_ms": 1000.0, "success_timestamps_by_tool": {"echo": [31.0]}}
    )

    monkeypatch.setattr(admin_api, "TRAJECTORY_STORE", _Store())
    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", _Registry())
    monkeypatch.setattr("tools.admin.strategy_helpers._state.TOOL_REGISTRY", _Registry())
    monkeypatch.setattr(
        admin_api,
        "config",
        _FakeConfig(
            {
                "observability": {
                    "tool_governance_slo_targets": {
                        "min_tool_success_rate": 0.7,
                        "max_tool_p95_latency_ms": 1500.0,
                        "max_budget_hit_rate": 0.2,
                        "max_circuit_recovery_ms": 7000.0,
                    }
                }
            }
        ),
    )

    payload = await admin_api.get_tool_governance_slo(limit=20)
    assert payload["status"] == "ok"
    slo = payload["slo"]
    assert slo["metrics"]["tool_calls"] == 3
    assert slo["metrics"]["tool_failures"] == 1
    assert slo["metrics"]["tool_p95_latency_ms"] == 1000.0
    assert slo["metrics"]["budget_hit_rate"] == 0.3333
    assert slo["metrics"]["circuit_recovery_p95_ms"] == 6000.0
    assert slo["checks"]["budget_hit_rate_ok"] is False
    assert slo["checks"]["circuit_recovery_ok"] is True

    out_path = tmp_path / "tool_governance_slo.md"
    exported = await admin_api.export_tool_governance_slo({"limit": 20, "output_path": str(out_path)})
    assert exported["status"] == "ok"
    assert out_path.is_file()
    text = out_path.read_text(encoding="utf-8")
    assert "Tool Governance SLO Report" in text
    assert "budget_hit_rate" in text


@pytest.mark.asyncio
async def test_trace_spec_baseline_panel_and_self_evolution_exports(monkeypatch, tmp_path: Path):
    class _Store:
        def list_recent(self, limit=200):
            return [{"run_id": "trace_run_1"}][:limit]

        def get_trajectory(self, run_id):
            if run_id != "trace_run_1":
                return None
            return {
                "events": [
                    {"action": "llm_request", "payload": {"trace_id": "trc_1", "request_id": "req_1"}},
                    {"action": "tool_call", "payload": {"trace_id": "trc_1", "tool_call_id": "tc_1", "tool": "echo"}},
                    {"action": "workflow_step", "payload": {"trace_id": "trc_1", "workflow_id": "wf_1", "node_id": "n1"}},
                ]
            }

    monkeypatch.setattr("tools.admin.observability.get_trajectory_store", lambda: _Store())
    monkeypatch.setattr("tools.admin.observability.TRAJECTORY_STORE", _Store())
    monkeypatch.setattr(
        "tools.admin.observability._build_tool_governance_slo",
        lambda limit=200: {"metrics": {"tool_success_rate": 0.98}, "checks": {"tool_success_rate_ok": True}, "passed": True},
    )
    monkeypatch.setattr(
        "tools.admin.system._build_workflow_observability_metrics",
        lambda limit=200: {"total_runs": 8, "failures": 1, "success_rate": 0.875, "p95_latency_ms": 2100.0},
    )
    monkeypatch.setattr(
        "tools.admin.system._build_persona_consistency_weekly_report",
        lambda window_days=7, source="persona_eval": {
            "current_window": {"consistency_score_avg": 0.86},
            "trend": {"direction": "improving"},
        },
    )
    monkeypatch.setattr(
        "tools.admin.system._build_training_gain_summary",
        lambda limit=50: {"job_count": 2, "avg_score": 0.72, "latest_score": 0.75, "latest_score_delta_vs_prev": 0.04},
    )

    trace_spec = await admin_api.get_observability_trace_spec(limit=20)
    assert trace_spec["status"] == "ok"
    assert trace_spec["trace_spec"]["links"]["full_chain_trace_count"] == 1
    assert trace_spec["trace_spec"]["coverage"]["trace_id_coverage_rate"] == 1.0

    trace_path = tmp_path / "trace_spec.md"
    exported_trace = await admin_api.export_observability_trace_spec({"limit": 20, "output_path": str(trace_path)})
    assert exported_trace["status"] == "ok"
    assert trace_path.is_file()
    assert "Unified Trace Spec Report" in trace_path.read_text(encoding="utf-8")

    baseline = await admin_api.get_observability_baseline_panel(limit=20, window_days=7)
    assert baseline["status"] == "ok"
    assert baseline["panel"]["metrics"]["training_avg_score"] == 0.72

    panel_path = tmp_path / "baseline_panel.md"
    exported_panel = await admin_api.export_observability_baseline_panel(
        {"limit": 20, "window_days": 7, "output_path": str(panel_path)}
    )
    assert exported_panel["status"] == "ok"
    assert panel_path.is_file()
    assert "Alignment Baseline Panel" in panel_path.read_text(encoding="utf-8")

    offline = await admin_api.get_self_evolution_offline_replay(case_limit=5)
    assert offline["status"] == "ok"
    assert offline["report"]["dataset_size"] == 5
    assert "success_rate" in offline["report"]["delta"]

    experiment_path = tmp_path / "self_evo.md"
    exported_experiment = await admin_api.export_self_evolution_offline_replay(
        {"case_limit": 5, "output_path": str(experiment_path)}
    )
    assert exported_experiment["status"] == "ok"
    assert experiment_path.is_file()
    assert "Self-Evolution Offline Replay Report" in experiment_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_deployment_target_crud_and_status_endpoints(monkeypatch):
    class _Registry:
        def __init__(self):
            self.providers = {
                "openai": {"base_url": "https://api.openai.com/v1", "api_key": "sk", "default_model": "gpt-4o"}
            }
            self.targets = {}

        def get_provider(self, name):
            return dict(self.providers.get(name, {}))

        def list_redacted_deployment_targets(self):
            out = {}
            for k, v in self.targets.items():
                item = dict(v)
                if "api_key" in item:
                    item["api_key"] = "***" if item.get("api_key") else ""
                out[k] = item
            return out

        def get_deployment_target(self, target_id):
            return dict(self.targets.get(target_id, {}))

        def upsert_deployment_target(self, target_id, cfg):
            self.targets[target_id] = dict(cfg)
            return dict(cfg)

        def delete_deployment_target(self, target_id):
            if target_id not in self.targets:
                return False
            del self.targets[target_id]
            return True

    class _Router:
        def get_status(self):
            return {"providers": [{"name": "openai_primary", "enabled": True}]}

    registry = _Registry()
    monkeypatch.setattr(admin_api, "get_provider_registry", lambda: registry)
    monkeypatch.setattr(admin_api, "LLM_ROUTER", _Router())

    created = await admin_api.create_deployment_target(
        {
            "target_id": "openai_primary",
            "target": {
                "provider": "openai",
                "type": "gateway",
                "enabled": True,
                "default_model": "gpt-4o-mini",
                "capacity_rpm": 200,
            },
        }
    )
    assert created["status"] == "ok"

    listed = await admin_api.list_deployment_targets()
    assert listed["status"] == "ok"
    assert "openai_primary" in listed["targets"]

    status = await admin_api.get_deployment_targets_status()
    assert status["status"] == "ok"
    assert status["enabled_targets"] == 1
    assert status["router_enabled"] is True

    updated = await admin_api.update_deployment_target(
        "openai_primary",
        {"target": {"provider": "openai", "enabled": False, "type": "gateway"}},
    )
    assert updated["status"] == "ok"
    assert registry.targets["openai_primary"]["enabled"] is False

    deleted = await admin_api.delete_deployment_target("openai_primary")
    assert deleted["status"] == "ok"
    assert "openai_primary" not in registry.targets


@pytest.mark.asyncio
async def test_deployment_target_health_endpoint(monkeypatch):
    class _Router:
        async def probe_routes(self, active=False, timeout_seconds=3.0):
            return [
                {
                    "name": "openai_primary",
                    "provider_name": "openai",
                    "healthy": True,
                    "active": bool(active),
                    "timeout_seconds": float(timeout_seconds),
                }
            ]

    monkeypatch.setattr("tools.admin.deployment.LLM_ROUTER", _Router())
    payload = await admin_api.probe_deployment_targets(active=True, timeout_seconds=1.5)
    assert payload["status"] == "ok"
    assert payload["active"] is True
    assert payload["probes"][0]["name"] == "openai_primary"
    assert payload["probes"][0]["healthy"] is True


@pytest.mark.asyncio
async def test_persona_eval_endpoints(monkeypatch, tmp_path):
    from eval.persona_consistency import PersonaConsistencyManager

    manager = PersonaConsistencyManager(base_dir=tmp_path / "persona_eval")
    monkeypatch.setattr(admin_api, "_get_persona_eval_manager", lambda: manager)
    monkeypatch.setattr(admin_api, "config", _FakeConfig({"personality": {"system_prompt": "You are Gazer"}}))

    built = await admin_api.build_persona_eval_dataset({"name": "core_persona"})
    assert built["status"] == "ok"
    dataset_id = built["dataset"]["id"]

    listed = await admin_api.list_persona_eval_datasets(limit=10)
    assert listed["status"] == "ok"
    assert listed["total"] == 1

    report = await admin_api.run_persona_eval_dataset(
        dataset_id,
        {
            "outputs": {
                "tone_warm": "Good morning, I'm Gazer.",
                "identity_consistency": "I am Gazer, your AI companion.",
                "safety_consistency": "I can't do that unsafe action, here is a safer way.",
            }
        },
    )
    assert report["status"] == "ok"
    assert report["report"]["consistency_score"] >= 0.8
