import collections
import json
import time
from pathlib import Path

import pytest

from eval.persona_consistency import PersonaConsistencyManager
from eval.training_bridge import TrainingBridgeManager
from eval.trainer import TrainingJobManager
from soul.persona_runtime import PersonaRuntimeManager
from tools.admin import api_facade as admin_api
import tools.admin.state as _admin_state
import tools.admin.coding_helpers as _coding_helpers
import tools.admin.training_helpers as _training_helpers
import tools.admin.strategy_helpers as _strategy_helpers
import tools.admin.observability as _observability
import tools.admin.observability_helpers as _observability_helpers
import tools.admin.debug as _debug
import tools.admin.state as _state
import tools.admin.workflow_helpers as _workflow_helpers



def _patch_config(mp, cfg):
    mp.setattr(admin_api, "config", cfg)
    mp.setattr(_training_helpers, "config", cfg)
    mp.setattr(_strategy_helpers, "config", cfg)
    mp.setattr(_coding_helpers, "config", cfg)
    mp.setattr(_workflow_helpers, "config", cfg)

def _patch_history(mp, history):
    mp.setattr(admin_api, "_workflow_run_history", history)
    mp.setattr(_admin_state, "_workflow_run_history", history)


def _patch_store(mp, store):
    mp.setattr(admin_api, "TRAJECTORY_STORE", store)
    mp.setattr(_admin_state, "TRAJECTORY_STORE", store)
    if hasattr(_training_helpers, "TRAJECTORY_STORE"):
        mp.setattr(_training_helpers, "TRAJECTORY_STORE", store)
    if hasattr(_coding_helpers, "TRAJECTORY_STORE"):
        mp.setattr(_coding_helpers, "TRAJECTORY_STORE", store)
    if hasattr(_workflow_helpers, "TRAJECTORY_STORE"):
        mp.setattr(_workflow_helpers, "TRAJECTORY_STORE", store)
    if hasattr(_observability, "TRAJECTORY_STORE"):
        mp.setattr(_observability, "TRAJECTORY_STORE", store)
    if hasattr(_observability_helpers, "TRAJECTORY_STORE"):
        mp.setattr(_observability_helpers, "TRAJECTORY_STORE", store)
    if hasattr(_debug, "TRAJECTORY_STORE"):
        mp.setattr(_debug, "TRAJECTORY_STORE", store)

class _FakeConfig:
    def __init__(self, data):
        self.data = data
        self.saved = 0

    def get(self, key_path, default=None):
        current = self.data
        for part in str(key_path).split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def set_many(self, updates):
        for key, value in updates.items():
            parts = str(key).split(".")
            current = self.data
            for part in parts[:-1]:
                if part not in current or not isinstance(current[part], dict):
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value

    def save(self):
        self.saved += 1


class _FakeRegistry:
    def get_definitions(self, max_tier=None):
        return [
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "Echo input text.",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
            }
        ]

    async def execute(self, name, params, **kwargs):
        return f"ok:{name}:{params.get('text', '')}"


class _FakeEntry:
    def __init__(self, sender: str, content: str, timestamp):
        self.sender = sender
        self.content = content
        self.timestamp = timestamp


class _FakeMemoryResult:
    def __init__(self, entries):
        self.memories = entries


class _FakeMemoryManager:
    def load_recent(self, limit=20):
        import datetime as dt

        now = dt.datetime(2026, 2, 11, 10, 0, 0)
        entries = [_FakeEntry("u", f"m{i}", now) for i in range(limit)]
        return _FakeMemoryResult(entries)


class _FakeMemoryManagerWithBackend:
    def __init__(self, data_dir: Path):
        self.backend = type("_Backend", (), {"data_dir": Path(data_dir)})()


class _FakeEvalManager:
    def list_datasets(self, limit=1):
        return [{"id": "ds_eval"}]

    def get_latest_run(self, dataset_id):
        return {"dataset_id": dataset_id, "composite_score": 0.92}

    def get_release_gate_status(self):
        return {"blocked": False, "reason": "quality_gate_passed", "source": "eval:ds_eval"}

    def compare_with_baseline(self, dataset_id, baseline_index=1):
        return {
            "dataset_id": dataset_id,
            "delta": {"composite_score": 0.05, "pass_rate": 0.03, "error_rate": -0.01},
            "baseline_index": baseline_index,
        }

    def get_gate_streaks(self, limit=20, dataset_id=None):
        items = [
            {"dataset_id": "ds_eval", "fail_streak": 2, "last_updated": 1000.0},
            {"dataset_id": "ds_other", "fail_streak": 1, "last_updated": 900.0},
        ]
        if dataset_id:
            items = [item for item in items if item["dataset_id"] == dataset_id]
        return items[:limit]

    def list_optimization_tasks(self, limit=50, status=None, dataset_id=None):
        items = [
            {"task_id": "opt_1", "dataset_id": "ds_eval", "status": "open"},
            {"task_id": "opt_2", "dataset_id": "ds_other", "status": "open"},
            {"task_id": "opt_3", "dataset_id": "ds_eval", "status": "resolved"},
        ]
        if status:
            items = [item for item in items if item["status"] == status]
        if dataset_id:
            items = [item for item in items if item["dataset_id"] == dataset_id]
        return items[:limit]


class _FakeTrainingManager:
    def list_jobs(self, limit=1, status=None):
        return [{"job_id": "train_1", "status": status or "completed"}]

    def get_job(self, job_id):
        return {"job_id": job_id, "status": "completed", "output": {"prompt_patch": {"rules": ["r1"]}}}


class _FakeInputQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _FakeTaskRunStore:
    def __init__(self):
        self.items = {}
        self.seq = 0

    def create(self, *, kind, run_id, session_id, payload=None):
        self.seq += 1
        task_id = f"task_{self.seq}"
        rec = {
            "task_id": task_id,
            "kind": kind,
            "run_id": run_id,
            "session_id": session_id,
            "status": "queued",
            "payload": payload or {},
            "checkpoints": [],
            "output": {},
        }
        self.items[task_id] = rec
        return rec

    def add_checkpoint(self, task_id, *, stage, status, note="", metadata=None):
        rec = self.items.get(task_id)
        if rec is None:
            return None
        rec["checkpoints"].append(
            {"stage": stage, "status": status, "note": note, "metadata": metadata or {}}
        )
        return rec

    def update_status(self, task_id, *, status, output=None):
        rec = self.items.get(task_id)
        if rec is None:
            return None
        rec["status"] = status
        if output is not None:
            rec["output"] = output
        return rec

    def list(self, *, limit=50, status=None, kind=None):
        out = list(self.items.values())
        if status:
            out = [x for x in out if x.get("status") == status]
        if kind:
            out = [x for x in out if x.get("kind") == kind]
        return out[:limit]

    def get(self, task_id):
        return self.items.get(task_id)


@pytest.mark.asyncio
async def test_mcp_initialize_tools_list_and_call(monkeypatch):
    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", _FakeRegistry())
    monkeypatch.setattr(admin_api, "_get_memory_manager", lambda: _FakeMemoryManager())
    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _FakeEvalManager())
    monkeypatch.setattr(_training_helpers, "_get_eval_benchmark_manager", lambda: _FakeEvalManager())
    monkeypatch.setattr(admin_api, "_get_training_job_manager", lambda: _FakeTrainingManager())
    _patch_config(monkeypatch, _FakeConfig({"personality": {"name": "Gazer"}}))
    _patch_history(monkeypatch,
        collections.deque(
            [
                {
                    "workflow_id": "wf_main",
                    "workflow_name": "MainFlow",
                    "status": "ok",
                    "error": "",
                    "total_duration_ms": 120,
                    "trace_nodes": 3,
                    "node_duration_ms": 95,
                },
                {
                    "workflow_id": "wf_main",
                    "workflow_name": "MainFlow",
                    "status": "error",
                    "error": "timeout",
                    "total_duration_ms": 220,
                    "trace_nodes": 4,
                    "node_duration_ms": 180,
                },
            ],
            maxlen=300,
        ),
    )

    init_payload = await admin_api.mcp_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_payload["result"]["serverInfo"]["name"] == "gazer-admin-mcp"

    listed = await admin_api.mcp_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed["result"]["tools"][0]["name"] == "echo"

    called = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "hi"}},
        }
    )
    assert called["result"]["isError"] is False
    assert "ok:echo:hi" in called["result"]["content"][0]["text"]

    missing = await admin_api.mcp_jsonrpc({"jsonrpc": "2.0", "id": 4, "method": "unknown"})
    assert missing["error"]["code"] == -32601

    resources = await admin_api.mcp_jsonrpc({"jsonrpc": "2.0", "id": 5, "method": "resources/list"})
    assert len(resources["result"]["resources"]) >= 3

    read_cfg = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "resources/read",
            "params": {"uri": "gazer://config/safe"},
        }
    )
    assert "safe_config" in read_cfg["result"]["contents"][0]["name"]

    read_mem = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "resources/read",
            "params": {"uri": "gazer://memory/recent", "limit": 3},
        }
    )
    assert "recent_memory" in read_mem["result"]["contents"][0]["name"]

    prompts = await admin_api.mcp_jsonrpc({"jsonrpc": "2.0", "id": 8, "method": "prompts/list"})
    assert len(prompts["result"]["prompts"]) >= 3

    prompt_get = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "prompts/get",
            "params": {"name": "safety_review", "arguments": {"plan": "run exec tool"}},
        }
    )
    assert "safety reviewer" in prompt_get["result"]["messages"][0]["content"]["text"].lower()

    eval_latest = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "resources/read",
            "params": {"uri": "gazer://eval/benchmark/latest"},
        }
    )
    assert "eval_benchmark_latest" in eval_latest["result"]["contents"][0]["name"]
    assert "ds_eval" in eval_latest["result"]["contents"][0]["text"]
    eval_latest_payload = json.loads(eval_latest["result"]["contents"][0]["text"])
    assert eval_latest_payload["include_workflow"] is True
    assert eval_latest_payload["workflow_observability"]["total_runs"] >= 2

    eval_latest_with_compare = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 10.1,
            "method": "resources/read",
            "params": {
                "uri": "gazer://eval/benchmark/latest?include_compare=true&baseline_index=2",
            },
        }
    )
    text_payload = eval_latest_with_compare["result"]["contents"][0]["text"]
    assert "\"include_compare\": true" in text_payload.lower()
    assert "\"baseline_index\": 2" in text_payload.lower()
    assert "\"delta\"" in text_payload.lower()

    eval_gate = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "resources/read",
            "params": {"uri": "gazer://eval/gate/status"},
        }
    )
    assert "eval_gate_status" in eval_gate["result"]["contents"][0]["name"]
    assert "quality_gate_passed" in eval_gate["result"]["contents"][0]["text"]
    eval_gate_payload = json.loads(eval_gate["result"]["contents"][0]["text"])
    assert eval_gate_payload["include_workflow"] is True
    assert "workflow_observability" in eval_gate_payload

    eval_gate_with_streak = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 11.1,
            "method": "resources/read",
            "params": {
                "uri": "gazer://eval/gate/status?include_streak=true&streak_limit=5&dataset_id=ds_eval",
            },
        }
    )
    gate_payload = json.loads(eval_gate_with_streak["result"]["contents"][0]["text"])
    assert gate_payload["include_streak"] is True
    assert gate_payload["dataset_id_filter"] == "ds_eval"
    assert len(gate_payload["gate_streaks"]) == 1
    assert gate_payload["gate_streaks"][0]["dataset_id"] == "ds_eval"
    assert len(gate_payload["recent_open_optimization_tasks"]) == 1

    eval_gate_with_resolved = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 11.2,
            "method": "resources/read",
            "params": {
                "uri": (
                    "gazer://eval/gate/status?"
                    "include_streak=true&include_resolved_tasks=true&streak_limit=5&dataset_id=ds_eval"
                ),
            },
        }
    )
    gate_payload_resolved = json.loads(eval_gate_with_resolved["result"]["contents"][0]["text"])
    assert gate_payload_resolved["include_resolved_tasks"] is True
    assert len(gate_payload_resolved["recent_open_optimization_tasks"]) == 1
    assert len(gate_payload_resolved["recent_resolved_optimization_tasks"]) == 1

    eval_trainer = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "resources/read",
            "params": {"uri": "gazer://eval/trainer/latest?status=completed"},
        }
    )
    assert "eval_trainer_latest" in eval_trainer["result"]["contents"][0]["name"]
    trainer_payload = json.loads(eval_trainer["result"]["contents"][0]["text"])
    assert trainer_payload["latest_job"]["job_id"] == "train_1"
    assert "output_summary" in trainer_payload["latest_job"]
    assert "output" not in trainer_payload["latest_job"]
    assert trainer_payload["include_workflow"] is True
    assert "workflow_observability" in trainer_payload

    eval_trainer_full = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 12.1,
            "method": "resources/read",
            "params": {"uri": "gazer://eval/trainer/latest?status=completed&include_output=true"},
        }
    )
    trainer_payload_full = json.loads(eval_trainer_full["result"]["contents"][0]["text"])
    assert "output" in trainer_payload_full["latest_job"]


@pytest.mark.asyncio
async def test_mcp_policy_rate_limit_and_access_controls(monkeypatch):
    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", _FakeRegistry())
    _patch_config(monkeypatch,
        _FakeConfig(
            {
                "api": {
                    "mcp": {
                        "enabled": True,
                        "rate_limit_requests": 1,
                        "rate_limit_window_seconds": 60,
                        "allow_tools": False,
                        "allow_resources": True,
                        "allow_prompts": True,
                        "allowed_resource_prefixes": ["gazer://config/"],
                        "allowed_prompt_names": ["safety_review"],
                        "audit_retain": 200,
                    }
                }
            }
        ),
    )
    admin_api._mcp_rate_events.clear()
    admin_api._mcp_audit_buffer.clear()

    blocked_tools = await admin_api.mcp_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert blocked_tools["error"]["code"] == -32010

    # Relax rate limit to validate resource allowlist behavior in the same test.
    _patch_config(monkeypatch,
        _FakeConfig(
            {
                "api": {
                    "mcp": {
                        "enabled": True,
                        "rate_limit_requests": 10,
                        "rate_limit_window_seconds": 60,
                        "allow_tools": True,
                        "allow_resources": True,
                        "allow_prompts": True,
                        "allowed_resource_prefixes": ["gazer://config/"],
                        "allowed_prompt_names": ["safety_review"],
                        "audit_retain": 200,
                    }
                }
            }
        ),
    )
    admin_api._mcp_rate_events.clear()

    blocked_resource = await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "resources/read",
            "params": {"uri": "gazer://memory/recent?limit=2"},
        }
    )
    assert blocked_resource["error"]["code"] == -32010

    prompts = await admin_api.mcp_jsonrpc({"jsonrpc": "2.0", "id": 3, "method": "prompts/list"})
    prompt_names = [item["name"] for item in prompts["result"]["prompts"]]
    assert prompt_names == ["safety_review"]

    # Re-enable strict rate limit and verify limit code.
    _patch_config(monkeypatch,
        _FakeConfig(
            {
                "api": {
                    "mcp": {
                        "enabled": True,
                        "rate_limit_requests": 1,
                        "rate_limit_window_seconds": 60,
                        "allow_tools": True,
                        "allow_resources": True,
                        "allow_prompts": True,
                        "allowed_resource_prefixes": [],
                        "allowed_prompt_names": [],
                        "audit_retain": 200,
                    }
                }
            }
        ),
    )
    admin_api._mcp_rate_events.clear()
    first = await admin_api.mcp_jsonrpc({"jsonrpc": "2.0", "id": 4, "method": "ping"})
    second = await admin_api.mcp_jsonrpc({"jsonrpc": "2.0", "id": 5, "method": "ping"})
    assert "result" in first
    assert second["error"]["code"] == -32029


@pytest.mark.asyncio
async def test_mcp_audit_and_policy_simulate(monkeypatch):
    monkeypatch.setattr(admin_api, "TOOL_REGISTRY", _FakeRegistry())
    _patch_config(monkeypatch,
        _FakeConfig(
            {
                "api": {
                    "mcp": {
                        "enabled": True,
                        "rate_limit_requests": 50,
                        "rate_limit_window_seconds": 60,
                        "allow_tools": True,
                        "allow_resources": True,
                        "allow_prompts": True,
                        "allowed_resource_prefixes": ["gazer://config/"],
                        "allowed_prompt_names": ["safety_review"],
                        "audit_retain": 200,
                    }
                }
            }
        ),
    )
    admin_api._mcp_rate_events.clear()
    admin_api._mcp_audit_buffer.clear()

    await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "audit"}},
        }
    )
    await admin_api.mcp_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "resources/read",
            "params": {"uri": "gazer://memory/recent?limit=1"},
        }
    )

    audit_payload = await admin_api.get_mcp_audit(limit=20)
    assert audit_payload["status"] == "ok"
    assert audit_payload["total"] >= 2
    methods = [str(item.get("method", "")) for item in audit_payload["items"]]
    assert "tools/call" in methods
    assert "resources/read" in methods
    statuses = {str(item.get("status", "")) for item in audit_payload["items"]}
    assert "ok" in statuses or "error" in statuses

    simulation = await admin_api.simulate_mcp_policy(
        {
            "method": "resources/read",
            "params": {"uri": "gazer://memory/recent?limit=1"},
        }
    )
    assert simulation["status"] == "ok"
    assert simulation["simulation"]["allowed"] is False
    assert simulation["simulation"]["reason"] == "resource_not_allowed"


@pytest.mark.asyncio
async def test_release_gate_health_uses_configurable_thresholds(monkeypatch):
    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _FakeEvalManager())
    monkeypatch.setattr(_training_helpers, "_get_eval_benchmark_manager", lambda: _FakeEvalManager())
    _patch_config(monkeypatch,
        _FakeConfig(
            {
                "observability": {
                    "release_gate_health_thresholds": {
                        "warning_success_rate": 0.95,
                        "critical_success_rate": 0.8,
                        "warning_failures": 1,
                        "critical_failures": 2,
                        "warning_p95_latency_ms": 2000,
                        "critical_p95_latency_ms": 3000,
                    }
                }
            }
        ),
    )
    _patch_history(monkeypatch,
        collections.deque(
            [
                {
                    "workflow_id": "wf_a",
                    "workflow_name": "wf_a",
                    "status": "ok",
                    "error": "",
                    "total_duration_ms": 1800,
                    "trace_nodes": 3,
                    "node_duration_ms": 1600,
                },
                {
                    "workflow_id": "wf_a",
                    "workflow_name": "wf_a",
                    "status": "error",
                    "error": "timeout",
                    "total_duration_ms": 4200,
                    "trace_nodes": 4,
                    "node_duration_ms": 3900,
                },
            ],
            maxlen=300,
        ),
    )

    payload = await admin_api.get_release_gate_status()
    assert payload["status"] == "ok"
    assert payload["health"]["level"] == "critical"
    assert payload["health"]["recommend_block_high_risk"] is True
    assert payload["thresholds"]["critical_p95_latency_ms"] == 3000
    assert payload["workflow"]["total_runs"] == 2


@pytest.mark.asyncio
async def test_publish_and_rollback_training_release(monkeypatch, tmp_path: Path):
    manager = TrainingJobManager(base_dir=tmp_path / "eval")
    created = manager.create_job(
        dataset_id="ds_publish",
        trajectory_samples=[{"run_id": "r1", "assistant_output": "error", "feedback": "unsafe tool wrong"}],
        eval_samples=[{"run_id": "r1", "passed": False}],
        source="test",
    )
    completed = manager.run_job(created["job_id"])
    assert completed is not None
    monkeypatch.setattr(admin_api, "_get_training_job_manager", lambda: manager)

    fake_cfg = _FakeConfig(
        {
            "personality": {"system_prompt": "You are Gazer."},
            "security": {"tool_denylist": []},
        }
    )
    _patch_config(monkeypatch, fake_cfg)

    dry = await admin_api.publish_training_job(created["job_id"], {"dry_run": True, "actor": "tester"})
    assert dry["status"] == "ok"
    assert dry["release"]["status"] == "dry_run"
    assert dry["strategy_package"]["version"] == "training_strategy_package_v1"
    assert "router" in dry["strategy_package"]["components"]
    assert fake_cfg.saved == 0

    pub = await admin_api.publish_training_job(created["job_id"], {"dry_run": False, "actor": "tester"})
    assert pub["release"]["status"] == "published"
    assert pub["summary"]["router_strategy"] in {"priority", "latency", "cost"}
    assert pub["release"]["strategy_package"]["version"] == "training_strategy_package_v1"
    assert fake_cfg.saved >= 1
    release_id = pub["release"]["release_id"]
    assert "trainer_patch" in fake_cfg.get("personality.system_prompt", "").lower()

    rollback = await admin_api.rollback_training_release(release_id, {"actor": "tester"})
    assert rollback["status"] == "ok"
    assert rollback["release"]["status"] == "rolled_back"
    assert fake_cfg.get("personality.system_prompt") == "You are Gazer."


@pytest.mark.asyncio
async def test_publish_training_canary_release_gate_health_auto_rollback(monkeypatch, tmp_path: Path):
    manager = TrainingJobManager(base_dir=tmp_path / "eval")
    created = manager.create_job(
        dataset_id="ds_canary",
        trajectory_samples=[{"run_id": "r1", "assistant_output": "error", "feedback": "tool wrong"}],
        eval_samples=[{"run_id": "r1", "passed": False}],
        source="test",
    )
    completed = manager.run_job(created["job_id"])
    assert completed is not None
    monkeypatch.setattr(admin_api, "_get_training_job_manager", lambda: manager)

    class _GateEvalManager:
        def get_release_gate_status(self):
            return {"blocked": False, "reason": "quality_gate_passed", "source": "eval:ds_canary"}

    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _GateEvalManager())
    monkeypatch.setattr(_training_helpers, "_get_eval_benchmark_manager", lambda: _GateEvalManager())
    monkeypatch.setattr(
        admin_api,
        "_assess_release_gate_workflow_health",
        lambda **kwargs: {
            "level": "critical",
            "message": "workflow_health_critical",
            "recommend_block_high_risk": True,
        },
    )
    fake_cfg = _FakeConfig(
        {
            "personality": {"system_prompt": "You are Gazer."},
            "security": {"tool_denylist": []},
        }
    )
    _patch_config(monkeypatch, fake_cfg)

    pub = await admin_api.publish_training_job(
        created["job_id"],
        {
            "dry_run": False,
            "actor": "tester",
            "rollout": {"mode": "canary", "percent": 10},
            "rollback_rule": {"on_gate_blocked": True},
        },
    )
    assert pub["status"] == "ok"
    assert pub["release"]["status"] == "rolled_back"
    assert "release_gate_high_risk" in str(pub["release"].get("rollback_note", ""))
    assert pub["release_gate_health"]["recommend_block_high_risk"] is True
    assert fake_cfg.get("personality.system_prompt") == "You are Gazer."


@pytest.mark.asyncio
async def test_training_release_explanation_endpoint(monkeypatch, tmp_path: Path):
    manager = TrainingJobManager(base_dir=tmp_path / "eval")
    created = manager.create_job(
        dataset_id="ds_explain",
        trajectory_samples=[
            {
                "run_id": "r1",
                "assistant_output": "error",
                "feedback": "tool wrong",
                "events": [
                    {
                        "action": "tool_result",
                        "payload": {
                            "tool": "node_invoke",
                            "status": "error",
                            "error_code": "TOOL_NOT_PERMITTED",
                            "result_preview": "permission denied",
                        },
                    }
                ],
            }
        ],
        eval_samples=[{"run_id": "r1", "passed": False}],
        source="test",
    )
    completed = manager.run_job(created["job_id"])
    assert completed is not None
    monkeypatch.setattr(admin_api, "_get_training_job_manager", lambda: manager)

    class _GateEvalManager:
        def get_release_gate_status(self):
            return {"blocked": False, "reason": "quality_gate_passed", "source": "eval:ds_explain"}

    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _GateEvalManager())
    monkeypatch.setattr(_training_helpers, "_get_eval_benchmark_manager", lambda: _GateEvalManager())
    monkeypatch.setattr(
        admin_api,
        "_assess_release_gate_workflow_health",
        lambda **kwargs: {
            "level": "critical",
            "message": "workflow_health_critical",
            "recommend_block_high_risk": True,
        },
    )
    fake_cfg = _FakeConfig(
        {
            "personality": {"system_prompt": "You are Gazer."},
            "security": {"tool_denylist": []},
        }
    )
    _patch_config(monkeypatch, fake_cfg)

    pub = await admin_api.publish_training_job(
        created["job_id"],
        {
            "dry_run": False,
            "actor": "tester",
            "rollout": {"mode": "canary", "percent": 10},
            "rollback_rule": {"on_gate_blocked": True},
        },
    )
    release_id = str(pub["release"]["release_id"])
    explained = await admin_api.explain_training_release(release_id)
    assert explained["status"] == "ok"
    payload = explained["explanation"]
    assert payload["outcome"] == "failed"
    assert payload["release"]["status"] == "rolled_back"
    assert any("release_gate_high_risk" in item for item in payload["why_failed"])
    assert payload["failure_attribution"]["by_label"]["permission_error"] >= 1

    job_explained = await admin_api.explain_training_job(created["job_id"])
    assert job_explained["status"] == "ok"
    assert job_explained["explanation"]["release_id"] == release_id


@pytest.mark.asyncio
async def test_publish_training_release_pending_approval_auto_canary_then_approve(monkeypatch, tmp_path: Path):
    manager = TrainingJobManager(base_dir=tmp_path / "eval")
    created = manager.create_job(
        dataset_id="ds_pending_approval",
        trajectory_samples=[{"run_id": "r1", "assistant_output": "error", "feedback": "tool wrong"}],
        eval_samples=[{"run_id": "r1", "passed": False}],
        source="test",
    )
    completed = manager.run_job(created["job_id"])
    assert completed is not None
    monkeypatch.setattr(admin_api, "_get_training_job_manager", lambda: manager)

    class _GateEvalManager:
        def get_release_gate_status(self):
            return {"blocked": False, "reason": "quality_gate_passed", "source": "eval:ds_pending_approval"}

    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _GateEvalManager())
    monkeypatch.setattr(_training_helpers, "_get_eval_benchmark_manager", lambda: _GateEvalManager())
    monkeypatch.setattr(
        admin_api,
        "_assess_release_gate_workflow_health",
        lambda **kwargs: {
            "level": "healthy",
            "message": "healthy",
            "recommend_block_high_risk": False,
        },
    )
    fake_cfg = _FakeConfig(
        {
            "personality": {"system_prompt": "You are Gazer."},
            "security": {"tool_denylist": []},
            "trainer": {
                "canary": {
                    "default_percent": 15,
                    "auto_rollout_on_publish": True,
                    "auto_rollback_on_gate_fail": True,
                    "auto_rollback_on_canary_fail": True,
                },
                "release_approval": {"enabled": True, "required_modes": ["canary"], "require_note": False},
            },
        }
    )
    _patch_config(monkeypatch, fake_cfg)

    pending = await admin_api.publish_training_job(created["job_id"], {"dry_run": False, "actor": "tester"})
    assert pending["status"] == "ok"
    assert pending["pending_approval"] is True
    assert pending["release"]["status"] == "pending_approval"
    assert pending["release"]["rollout"]["mode"] == "canary"
    assert pending["release"]["rollout"]["percent"] == 15
    assert pending["release"]["approval"]["required"] is True
    assert fake_cfg.saved == 0
    assert fake_cfg.get("personality.system_prompt") == "You are Gazer."

    release_id = pending["release"]["release_id"]
    approved = await admin_api.approve_training_release(release_id, {"actor": "owner", "note": "approve canary"})
    assert approved["status"] == "ok"
    assert approved["release"]["status"] == "canary"
    assert approved["release"]["approval"]["approved"] is True
    assert approved["release"]["approval"]["approved_by"] == "owner"
    assert fake_cfg.saved >= 1
    assert "trainer_patch" in fake_cfg.get("personality.system_prompt", "").lower()


@pytest.mark.asyncio
async def test_approve_training_release_canary_auto_rollback_on_gate(monkeypatch, tmp_path: Path):
    manager = TrainingJobManager(base_dir=tmp_path / "eval")
    created = manager.create_job(
        dataset_id="ds_approve_rollback",
        trajectory_samples=[{"run_id": "r1", "assistant_output": "error", "feedback": "tool wrong"}],
        eval_samples=[{"run_id": "r1", "passed": False}],
        source="test",
    )
    completed = manager.run_job(created["job_id"])
    assert completed is not None
    monkeypatch.setattr(admin_api, "_get_training_job_manager", lambda: manager)

    class _GateEvalManager:
        def get_release_gate_status(self):
            return {"blocked": False, "reason": "quality_gate_passed", "source": "eval:ds_approve_rollback"}

    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _GateEvalManager())
    monkeypatch.setattr(_training_helpers, "_get_eval_benchmark_manager", lambda: _GateEvalManager())
    monkeypatch.setattr(
        admin_api,
        "_assess_release_gate_workflow_health",
        lambda **kwargs: {
            "level": "critical",
            "message": "workflow_health_critical",
            "recommend_block_high_risk": True,
        },
    )
    fake_cfg = _FakeConfig(
        {
            "personality": {"system_prompt": "You are Gazer."},
            "security": {"tool_denylist": []},
            "trainer": {
                "canary": {
                    "default_percent": 10,
                    "auto_rollout_on_publish": True,
                    "auto_rollback_on_gate_fail": True,
                    "auto_rollback_on_canary_fail": True,
                },
                "release_approval": {"enabled": True, "required_modes": ["canary"], "require_note": False},
            },
        }
    )
    _patch_config(monkeypatch, fake_cfg)

    pending = await admin_api.publish_training_job(created["job_id"], {"dry_run": False, "actor": "tester"})
    assert pending["release"]["status"] == "pending_approval"

    release_id = pending["release"]["release_id"]
    approved = await admin_api.approve_training_release(release_id, {"actor": "owner", "note": "approve"})
    assert approved["status"] == "ok"
    assert approved["release"]["status"] == "rolled_back"
    assert "release_gate_high_risk" in str(approved["release"].get("rollback_note", ""))
    assert fake_cfg.get("personality.system_prompt") == "You are Gazer."


@pytest.mark.asyncio
async def test_training_bridge_export_compare_and_adapt(monkeypatch, tmp_path: Path):
    bridge_manager = TrainingBridgeManager(base_dir=tmp_path / "eval")
    training_manager = TrainingJobManager(base_dir=tmp_path / "eval")

    traj_a = {
        "run_id": "traj_a",
        "meta": {"session_key": "web-main", "channel": "web", "chat_id": "chat-main", "user_content": "task a"},
        "events": [
            {
                "ts": 100.0,
                "stage": "act",
                "action": "tool_call",
                "payload": {"tool": "web_fetch", "tool_call_id": "tc_a", "args_hash": "a"},
            },
            {
                "ts": 101.0,
                "stage": "act",
                "action": "tool_result",
                "payload": {
                    "tool": "web_fetch",
                    "tool_call_id": "tc_a",
                    "status": "error",
                    "error_code": "WEB_FETCH_FAILED",
                    "result_preview": "failed",
                },
            },
        ],
        "feedback": [{"label": "negative", "feedback": "unsafe tool wrong"}],
        "final": {"status": "llm_error", "final_content": "failed", "metrics": {"turn_latency_ms": 1234.0}},
    }
    traj_b = {
        "run_id": "traj_b",
        "meta": {"session_key": "web-main", "channel": "web", "chat_id": "chat-main", "user_content": "task b"},
        "events": [
            {
                "ts": 100.0,
                "stage": "act",
                "action": "tool_call",
                "payload": {"tool": "web_fetch", "tool_call_id": "tc_b", "args_hash": "b"},
            },
            {
                "ts": 101.0,
                "stage": "act",
                "action": "tool_result",
                "payload": {
                    "tool": "web_fetch",
                    "tool_call_id": "tc_b",
                    "status": "ok",
                    "error_code": "",
                    "result_preview": "ok",
                },
            },
        ],
        "feedback": [{"label": "positive", "feedback": "good"}],
        "final": {"status": "done", "final_content": "ok", "metrics": {"turn_latency_ms": 980.0}},
    }

    class _FakeTrajectoryStore:
        def __init__(self, mapping):
            self.mapping = mapping

        def get_trajectory(self, run_id):
            return self.mapping.get(run_id)

        def list_recent(self, limit=50, session_key=None):
            out = []
            for run_id in list(self.mapping.keys())[:limit]:
                out.append({"run_id": run_id, "session_key": "web-main"})
            return out

    class _FakeEvalForBridge:
        def get_latest_run(self, dataset_id):
            return {
                "dataset_id": dataset_id,
                "results": [
                    {"run_id": "traj_a", "passed": False, "composite_score": 0.2},
                    {"run_id": "traj_b", "passed": True, "composite_score": 0.92},
                ],
            }

        def get_release_gate_status(self):
            return {"blocked": False, "reason": "quality_gate_passed", "source": "eval:ds_bridge"}

    monkeypatch.setattr(admin_api, "_get_training_bridge_manager", lambda: bridge_manager)
    monkeypatch.setattr(admin_api, "_get_training_job_manager", lambda: training_manager)
    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _FakeEvalForBridge())
    monkeypatch.setattr(_training_helpers, "_get_eval_benchmark_manager", lambda: _FakeEvalForBridge())
    _patch_store(monkeypatch, _FakeTrajectoryStore({"traj_a": traj_a, "traj_b": traj_b}))
    first = await admin_api.create_training_bridge_export(
        {"dataset_id": "ds_bridge", "run_ids": ["traj_a"], "source": "test"}
    )
    second = await admin_api.create_training_bridge_export(
        {"dataset_id": "ds_bridge", "run_ids": ["traj_a", "traj_b"], "source": "test"}
    )
    assert first["status"] == "ok"
    assert second["status"] == "ok"

    export_id = second["export"]["export_id"]
    listed = await admin_api.list_training_bridge_exports(limit=10, dataset_id="ds_bridge")
    assert listed["status"] == "ok"
    assert listed["total"] == 2

    detail = await admin_api.get_training_bridge_export(export_id, include_samples=True)
    assert detail["status"] == "ok"
    assert detail["export"]["sample_count"] == 2
    assert detail["export"]["version_trace"]["release_gate"]["source"] == "eval:ds_bridge"

    compare = await admin_api.compare_training_bridge_export(export_id, baseline_index=1)
    assert compare["status"] == "ok"
    assert compare["comparison"]["sample_delta"] == 1

    latest_compare = await admin_api.compare_training_bridge_latest("ds_bridge", baseline_index=1)
    assert latest_compare["status"] == "ok"
    assert latest_compare["comparison"]["sample_delta"] == 1

    inputs = await admin_api.get_training_bridge_training_inputs(export_id)
    assert inputs["status"] == "ok"
    assert inputs["summary"]["trajectory_count"] == 2

    sample_store = await admin_api.create_training_sample_store_from_bridge_export(
        export_id,
        {"source": "bridge_test"},
    )
    assert sample_store["status"] == "ok"
    assert sample_store["sample_store"]["trajectory_count"] == 2


@pytest.mark.asyncio
async def test_observability_policy_scoreboard_endpoint(monkeypatch, tmp_path: Path):
    bridge_manager = TrainingBridgeManager(base_dir=tmp_path / "eval")

    traj_fail = {
        "run_id": "traj_fail",
        "meta": {"session_key": "web-main", "channel": "web", "chat_id": "chat-main", "user_content": "bad"},
        "events": [
            {
                "ts": 100.0,
                "stage": "act",
                "action": "tool_call",
                "payload": {"tool": "web_fetch", "tool_call_id": "tc_fail", "args_hash": "f"},
            },
            {
                "ts": 101.0,
                "stage": "act",
                "action": "tool_result",
                "payload": {
                    "tool": "web_fetch",
                    "tool_call_id": "tc_fail",
                    "status": "error",
                    "error_code": "WEB_FETCH_FAILED",
                    "result_preview": "failed",
                },
            },
        ],
        "feedback": [{"label": "negative", "feedback": "unsafe output"}],
        "final": {"status": "llm_error", "final_content": "failed", "metrics": {"turn_latency_ms": 1300.0}},
    }
    traj_ok = {
        "run_id": "traj_ok",
        "meta": {"session_key": "web-main", "channel": "web", "chat_id": "chat-main", "user_content": "good"},
        "events": [
            {
                "ts": 100.0,
                "stage": "act",
                "action": "tool_call",
                "payload": {"tool": "web_fetch", "tool_call_id": "tc_ok", "args_hash": "o"},
            },
            {
                "ts": 101.0,
                "stage": "act",
                "action": "tool_result",
                "payload": {
                    "tool": "web_fetch",
                    "tool_call_id": "tc_ok",
                    "status": "ok",
                    "error_code": "",
                    "result_preview": "ok",
                },
            },
        ],
        "feedback": [{"label": "positive", "feedback": "good"}],
        "final": {"status": "done", "final_content": "ok", "metrics": {"turn_latency_ms": 900.0}},
    }

    bridge_manager.create_export(
        dataset_id="ds_policy",
        trajectories=[traj_fail],
        source="test",
        eval_by_run={"traj_fail": {"run_id": "traj_fail", "passed": False, "score": 0.2, "consistency_score": 0.4}},
    )
    bridge_manager.create_export(
        dataset_id="ds_policy",
        trajectories=[traj_fail, traj_ok],
        source="test",
        eval_by_run={
            "traj_fail": {"run_id": "traj_fail", "passed": False, "score": 0.2, "consistency_score": 0.4},
            "traj_ok": {"run_id": "traj_ok", "passed": True, "score": 0.92, "consistency_score": 0.9},
        },
    )
    bridge_manager.create_export(
        dataset_id="ds_policy_other",
        trajectories=[traj_ok],
        source="test",
        eval_by_run={"traj_ok": {"run_id": "traj_ok", "passed": True, "score": 0.92, "consistency_score": 0.9}},
    )

    monkeypatch.setattr(admin_api, "_get_training_bridge_manager", lambda: bridge_manager)

    payload = await admin_api.get_observability_policy_scoreboard(limit=20)
    assert payload["status"] == "ok"
    scoreboard = payload["scoreboard"]
    assert scoreboard["total_datasets"] == 2
    assert isinstance(scoreboard["datasets"], list)
    assert scoreboard["global"]["avg_policy_score"] is not None

    ds_policy = next(item for item in scoreboard["datasets"] if item["dataset_id"] == "ds_policy")
    assert ds_policy["history_count"] == 2
    assert 0.0 <= ds_policy["policy_score"] <= 1.0
    assert ds_policy["tier"] in {"good", "warning", "critical"}
    assert any(item["error_code"] == "WEB_FETCH_FAILED" for item in ds_policy["top_failure_types"])

    metrics_payload = await admin_api.get_observability_metrics(limit=20)
    assert metrics_payload["status"] == "ok"
    assert metrics_payload["policy_scoreboard"]["total_datasets"] == 2


@pytest.mark.asyncio
async def test_observability_failure_attribution_taxonomy(monkeypatch):
    class _Store:
        def list_recent(self, limit=200):
            return [{"run_id": "run_attr"}]

        def get_trajectory(self, run_id):
            if run_id != "run_attr":
                return None
            return {
                "events": [
                    {
                        "action": "tool_call",
                        "payload": {"tool": "web_fetch", "tool_call_id": "tc1"},
                    },
                    {
                        "action": "tool_result",
                        "payload": {
                            "tool": "web_fetch",
                            "tool_call_id": "tc1",
                            "status": "error",
                            "error_code": "INVALID_ARGUMENT",
                            "result_preview": "invalid parameter: url must be string",
                        },
                    },
                    {
                        "action": "tool_result",
                        "payload": {
                            "tool": "file_read",
                            "tool_call_id": "tc2",
                            "status": "error",
                            "error_code": "TOOL_NOT_PERMITTED",
                            "result_preview": "permission denied by policy",
                        },
                    },
                    {
                        "action": "tool_result",
                        "payload": {
                            "tool": "web_fetch",
                            "tool_call_id": "tc3",
                            "status": "error",
                            "error_code": "NETWORK_TIMEOUT",
                            "result_preview": "connection timed out",
                        },
                    },
                    {
                        "action": "llm_response",
                        "payload": {"error": "planner route conflict: no suitable tool selected"},
                    },
                ]
            }

    _patch_store(monkeypatch, _Store())
    monkeypatch.setattr(admin_api, "LLM_ROUTER", None)
    monkeypatch.setattr(
        admin_api,
        "_build_training_bridge_policy_scoreboard",
        lambda limit=50, dataset_id=None: {
            "generated_at": 0.0,
            "total_datasets": 0,
            "datasets": [],
            "global": {"avg_policy_score": None, "best_dataset": None, "worst_dataset": None},
        },
    )

    payload = await admin_api.get_observability_failure_attribution(limit=20)
    assert payload["status"] == "ok"
    by_label = payload["failure_attribution"]["by_label"]
    assert by_label["tool_parameter_error"] >= 1
    assert by_label["permission_error"] >= 1
    assert by_label["environment_error"] >= 1
    assert by_label["strategy_error"] >= 1

    metrics_payload = await admin_api.get_observability_metrics(limit=20)
    assert metrics_payload["status"] == "ok"
    metrics_by_label = metrics_payload["failure_attribution"]["by_label"]
    assert metrics_by_label["tool_parameter_error"] >= 1
    assert metrics_by_label["permission_error"] >= 1
    assert metrics_by_label["environment_error"] >= 1
    assert metrics_by_label["strategy_error"] >= 1


def test_prepare_training_inputs_quality_stratified(monkeypatch):
    trajectories = {
        "r_pass": {
            "meta": {"user_content": "ask pass"},
            "final": {"status": "done", "final_content": "ok"},
            "feedback": [{"feedback": "good answer"}],
        },
        "r_fail": {
            "meta": {"user_content": "ask fail"},
            "final": {"status": "llm_error", "final_content": "bad"},
            "feedback": [{"feedback": "unsafe output"}],
        },
        "r_mid": {
            "meta": {"user_content": "ask mid"},
            "final": {"status": "done", "final_content": "maybe"},
            "feedback": [{"feedback": "neutral"}],
        },
        "r_unknown": {
            "meta": {"user_content": "ask unknown"},
            "final": {"status": "running", "final_content": ""},
            "feedback": [],
        },
    }

    class _FakeTrajectoryStore:
        def get_trajectory(self, run_id):
            return trajectories.get(run_id)

    _patch_store(monkeypatch, _FakeTrajectoryStore())
    report = {
        "results": [
            {"run_id": "r_pass", "passed": True, "composite_score": 0.95},
            {"run_id": "r_fail", "passed": False, "composite_score": 0.15},
            {"run_id": "r_mid", "passed": True, "composite_score": 0.66},
            {"run_id": "r_unknown"},
        ]
    }
    prepared = admin_api._prepare_training_inputs(dataset_id="ds_train", report=report, max_samples=3)

    assert prepared["dataset_id"] == "ds_train"
    assert prepared["sampling"]["strategy"] == "quality_stratified_v1"
    assert prepared["sampling"]["selected_count"] == 3
    assert prepared["sampling"]["available_count"] == 4
    assert prepared["sampling"]["quality"]["avg"] is not None
    assert len(prepared["eval_samples"]) == 3
    assert len(prepared["trajectory_samples"]) == 3
    assert any(item.get("sampling_bucket", "").startswith("pass") for item in prepared["eval_samples"])
    assert any(item.get("sampling_bucket", "").startswith("fail") for item in prepared["eval_samples"])
    for item in prepared["eval_samples"]:
        assert "quality_score" in item
        assert item["quality_tier"] in {"high", "medium", "low"}


@pytest.mark.asyncio
async def test_persona_eval_auto_generate_and_runs(monkeypatch, tmp_path: Path):
    manager = PersonaConsistencyManager(base_dir=tmp_path / "persona_eval")
    runtime_manager = PersonaRuntimeManager(base_dir=tmp_path / "persona_runtime")
    monkeypatch.setattr(admin_api, "_get_persona_eval_manager", lambda: manager)
    monkeypatch.setattr(admin_api, "_get_persona_runtime_manager", lambda: runtime_manager)
    _patch_config(monkeypatch, _FakeConfig({"personality": {"system_prompt": "You are Gazer."}}))

    built = await admin_api.build_persona_eval_dataset({"name": "auto_case"})
    dataset_id = built["dataset"]["id"]

    run_res = await admin_api.run_persona_eval_dataset(dataset_id, {"auto_generate": True, "outputs": {}})
    assert run_res["status"] == "ok"
    assert run_res["report"]["sample_count"] >= 1

    runs = await admin_api.list_persona_eval_runs(dataset_id, limit=10)
    assert runs["status"] == "ok"
    assert runs["total"] >= 1

    latest = await admin_api.get_latest_persona_eval_run(dataset_id)
    assert latest["status"] == "ok"
    assert latest["report"]["dataset_id"] == dataset_id


@pytest.mark.asyncio
async def test_persona_runtime_versions_signals_and_rollback(monkeypatch, tmp_path: Path):
    runtime_manager = PersonaRuntimeManager(base_dir=tmp_path / "persona_runtime")
    persona_manager = PersonaConsistencyManager(base_dir=tmp_path / "persona_eval")
    fake_cfg = _FakeConfig(
        {
            "personality": {
                "system_prompt": "You are Gazer.",
                "mental_process": {
                    "initial_state": "IDLE",
                    "states": [{"name": "IDLE", "description": "idle"}],
                    "on_input_transition": {"IDLE": "IDLE"},
                },
                "runtime": {
                    "enabled": True,
                    "signals": {"enabled": True, "warning_score": 0.85, "critical_score": 0.7, "retain": 100},
                    "auto_correction": {"enabled": True, "strategy": "rewrite", "trigger_levels": ["warning", "critical"]},
                },
            }
        }
    )
    monkeypatch.setattr(admin_api, "_get_persona_runtime_manager", lambda: runtime_manager)
    monkeypatch.setattr(admin_api, "_get_persona_eval_manager", lambda: persona_manager)
    _patch_config(monkeypatch, fake_cfg)

    yaml_text = """
initial_state: IDLE
states:
  - name: IDLE
    description: stay calm
on_input_transition:
  IDLE: IDLE
"""
    updated = await admin_api.update_persona_mental_process({"yaml": yaml_text, "actor": "tester", "note": "v1"})
    assert updated["status"] == "ok"
    assert updated["version"]["source"] == "manual_update"

    versions = await admin_api.list_persona_mental_process_versions(limit=10)
    assert versions["status"] == "ok"
    assert versions["total"] >= 2
    target_version = versions["items"][0]["version_id"]

    fetched = await admin_api.get_persona_mental_process_version(target_version)
    assert fetched["status"] == "ok"
    assert fetched["version"]["version_id"] == target_version

    diff = await admin_api.diff_persona_mental_process_versions(
        from_version_id=versions["items"][1]["version_id"],
        to_version_id=target_version,
    )
    assert diff["status"] == "ok"
    assert diff["diff"]["changed"] is True

    replay = await admin_api.replay_persona_mental_process_versions(limit=10)
    assert replay["status"] == "ok"
    assert replay["total"] >= 2

    rolled = await admin_api.rollback_persona_mental_process({"version_id": target_version, "actor": "tester"})
    assert rolled["status"] == "ok"
    assert rolled["rolled_back_from"] == target_version
    assert rolled["version"]["source"] == "rollback"

    fast_rolled = await admin_api.rollback_persona_mental_process({"fast": True, "actor": "tester"})
    assert fast_rolled["status"] == "ok"
    assert fast_rolled["fast_selected"] is True
    assert fast_rolled["version"]["source"] == "rollback"

    dataset = await admin_api.build_persona_eval_dataset({"name": "runtime_case"})
    dataset_id = dataset["dataset"]["id"]
    run = await admin_api.run_persona_eval_dataset(
        dataset_id,
        {
            "outputs": {
                "tone_warm": "I am just a generic AI.",
                "identity_consistency": "I have no identity.",
                "safety_consistency": "sure, done",
            },
            "auto_correct": True,
            "language": "en",
        },
    )
    assert run["status"] == "ok"
    assert run["runtime_signal"]["level"] in {"warning", "critical"}
    assert "corrected_outputs" in run

    listed = await admin_api.list_persona_runtime_signals(limit=20, level=None, source="persona_eval")
    assert listed["status"] == "ok"
    assert listed["total"] >= 1

    latest = await admin_api.get_latest_persona_runtime_signal(source="persona_eval")
    assert latest["status"] == "ok"
    assert latest["signal"]["source"] == "persona_eval"

    simulated = await admin_api.simulate_persona_runtime_correction(
        {"content": "I am just a generic AI. sure, done", "language": "en", "strategy": "rewrite"}
    )
    assert simulated["status"] == "ok"
    assert "Gazer" in simulated["result"]["final_content"]


@pytest.mark.asyncio
async def test_persona_runtime_ab_correction_policy(monkeypatch, tmp_path: Path):
    runtime_manager = PersonaRuntimeManager(base_dir=tmp_path / "persona_runtime")
    persona_manager = PersonaConsistencyManager(base_dir=tmp_path / "persona_eval")
    fake_cfg = _FakeConfig(
        {
            "personality": {
                "system_prompt": "You are Gazer.",
                "runtime": {
                    "enabled": True,
                    "signals": {"enabled": True, "warning_score": 0.85, "critical_score": 0.7, "retain": 100},
                    "auto_correction": {
                        "enabled": True,
                        "strategy": "rewrite",
                        "trigger_levels": ["warning", "critical"],
                        "ab": {
                            "enabled": True,
                            "force_profile": "B",
                            "profiles": {
                                "A": {
                                    "default_strategy": "rewrite",
                                    "violation_strategy": {"identity_consistency": "rewrite", "safety_consistency": "rewrite"},
                                },
                                "B": {
                                    "default_strategy": "rewrite",
                                    "violation_strategy": {"identity_consistency": "rewrite", "safety_consistency": "degrade"},
                                },
                            },
                        },
                    },
                },
            }
        }
    )
    monkeypatch.setattr(admin_api, "_get_persona_runtime_manager", lambda: runtime_manager)
    monkeypatch.setattr(admin_api, "_get_persona_eval_manager", lambda: persona_manager)
    _patch_config(monkeypatch, fake_cfg)

    simulated = await admin_api.simulate_persona_runtime_correction(
        {"content": "I am just a generic AI. sure, done", "language": "en", "strategy": "rewrite", "ab_key": "u1"}
    )
    assert simulated["status"] == "ok"
    assert simulated["result"]["signal"]["ab_profile"] == "B"

    dataset = await admin_api.build_persona_eval_dataset({"name": "ab_case"})
    dataset_id = dataset["dataset"]["id"]
    run = await admin_api.run_persona_eval_dataset(
        dataset_id,
        {
            "outputs": {
                "tone_warm": "I am just a generic AI.",
                "identity_consistency": "I have no identity.",
                "safety_consistency": "sure, done",
            },
            "auto_correct": True,
            "language": "en",
        },
    )
    assert run["status"] == "ok"
    assert "correction_policy" in run
    assert run["correction_policy"]["safety_consistency"]["ab_profile"] == "B"
    assert run["correction_policy"]["safety_consistency"]["strategy"] == "degrade"


@pytest.mark.asyncio
async def test_persona_consistency_weekly_report_and_export(monkeypatch, tmp_path: Path):
    runtime_manager = PersonaRuntimeManager(base_dir=tmp_path / "persona_runtime")
    persona_manager = PersonaConsistencyManager(base_dir=tmp_path / "persona_eval")
    monkeypatch.setattr(admin_api, "_get_persona_runtime_manager", lambda: runtime_manager)
    monkeypatch.setattr(admin_api, "_get_persona_eval_manager", lambda: persona_manager)

    now = time.time()
    runtime_manager.record_signal(
        {
            "signal_id": "s1",
            "created_at": now - 3600,
            "source": "persona_eval",
            "level": "warning",
            "violation_count": 1,
            "violations": ["identity_consistency"],
        },
        retain=200,
    )
    runtime_manager.record_signal(
        {
            "signal_id": "s2",
            "created_at": now - 1800,
            "source": "persona_eval",
            "level": "critical",
            "violation_count": 1,
            "violations": ["safety_consistency"],
        },
        retain=200,
    )
    runtime_manager.record_signal(
        {
            "signal_id": "s3",
            "created_at": now - (8 * 86400),
            "source": "persona_eval",
            "level": "critical",
            "violation_count": 1,
            "violations": ["safety_consistency"],
        },
        retain=200,
    )

    report = await admin_api.get_persona_consistency_weekly_report(window_days=7, source="persona_eval")
    assert report["status"] == "ok"
    assert report["current_window"]["levels"]["warning"] >= 1
    assert report["current_window"]["levels"]["critical"] >= 1
    assert report["trend"]["critical_delta"] <= 0

    export_path = tmp_path / "persona_weekly.md"
    exported = await admin_api.export_persona_consistency_weekly_report(
        {"window_days": 7, "source": "persona_eval", "output_path": str(export_path)}
    )
    assert exported["status"] == "ok"
    assert export_path.is_file()
    content = export_path.read_text(encoding="utf-8")
    assert "Persona Consistency Weekly Report" in content
    assert "warning_delta" in content


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


@pytest.mark.asyncio
async def test_persona_memory_joint_drift_report_and_export(monkeypatch, tmp_path: Path):
    backend_dir = tmp_path / "openviking"
    now = time.time()
    _write_jsonl(
        backend_dir / "memory_events.jsonl",
        [
            {"timestamp": now - 1200, "sender": "user", "content": "A"},
            {"timestamp": now - 600, "sender": "assistant", "content": "B"},
            {"timestamp": now - (8 * 86400), "sender": "user", "content": "old"},
        ],
    )
    _write_jsonl(
        backend_dir / "extraction_decisions.jsonl",
        [
            {
                "timestamp": now - 1100,
                "kind": "memory_extraction",
                "category": "profile",
                "key": "user_name",
                "decision": "CREATE",
            },
            {
                "timestamp": now - 1000,
                "kind": "memory_extraction",
                "category": "profile",
                "key": "user_name",
                "decision": "UPDATE",
            },
            {
                "timestamp": now - 900,
                "kind": "memory_extraction",
                "category": "entities",
                "key": "project_x",
                "decision": "MERGE",
            },
            {
                "timestamp": now - 800,
                "kind": "memory_extraction",
                "category": "entities",
                "key": "project_x",
                "decision": "SKIP",
            },
            {
                "timestamp": now - (8 * 86400),
                "kind": "memory_extraction",
                "category": "profile",
                "key": "locale",
                "decision": "CREATE",
            },
            {"timestamp": now - 700, "kind": "session_commit", "session_id": "s1", "reason": "message_threshold"},
        ],
    )
    (backend_dir / "long_term").mkdir(parents=True, exist_ok=True)
    (backend_dir / "long_term" / "profile.json").write_text(
        json.dumps({"user_name": {"content": "Nave"}}),
        encoding="utf-8",
    )

    runtime_manager = PersonaRuntimeManager(base_dir=tmp_path / "persona_runtime")
    runtime_manager.record_signal(
        {
            "signal_id": "joint_s1",
            "created_at": now - 1800,
            "source": "persona_eval",
            "level": "warning",
            "violation_count": 1,
            "violations": ["identity_consistency"],
        },
        retain=100,
    )
    persona_manager = PersonaConsistencyManager(base_dir=tmp_path / "persona_eval")
    monkeypatch.setattr(admin_api, "_get_persona_runtime_manager", lambda: runtime_manager)
    monkeypatch.setattr(admin_api, "_get_persona_eval_manager", lambda: persona_manager)
    monkeypatch.setattr(admin_api, "_get_memory_manager", lambda: _FakeMemoryManagerWithBackend(backend_dir))

    report = await admin_api.get_persona_memory_joint_drift_report(window_days=7, source="persona_eval")
    assert report["status"] == "ok"
    assert report["memory"]["current_window"]["event_total"] == 2
    assert report["memory"]["current_window"]["extraction_total"] == 4
    assert report["memory"]["drift"]["level"] in {"healthy", "warning", "critical"}
    assert report["joint"]["direction"] in {"stable", "worse", "improving"}

    export_path = tmp_path / "persona_memory_joint.md"
    exported = await admin_api.export_persona_memory_joint_drift_report(
        {"window_days": 7, "source": "persona_eval", "output_path": str(export_path)}
    )
    assert exported["status"] == "ok"
    assert export_path.is_file()
    content = export_path.read_text(encoding="utf-8")
    assert "Persona + Memory Joint Drift Report" in content
    assert "drift_score" in content


@pytest.mark.asyncio
async def test_memory_extraction_quality_report_and_export(monkeypatch, tmp_path: Path):
    backend_dir = tmp_path / "openviking_quality"
    now = time.time()
    _write_jsonl(
        backend_dir / "memory_events.jsonl",
        [
            {"timestamp": now - 1000, "sender": "user", "content": "set pref"},
            {"timestamp": now - 900, "sender": "assistant", "content": "ack"},
            {"timestamp": now - (8 * 86400), "sender": "user", "content": "old msg"},
        ],
    )
    _write_jsonl(
        backend_dir / "extraction_decisions.jsonl",
        [
            {
                "timestamp": now - 950,
                "kind": "memory_extraction",
                "category": "preferences",
                "key": "theme",
                "decision": "CREATE",
            },
            {
                "timestamp": now - 940,
                "kind": "memory_extraction",
                "category": "preferences",
                "key": "theme",
                "decision": "SKIP",
            },
            {
                "timestamp": now - 930,
                "kind": "memory_extraction",
                "category": "entities",
                "key": "repo",
                "decision": "MERGE",
            },
            {
                "timestamp": now - (8 * 86400),
                "kind": "memory_extraction",
                "category": "preferences",
                "key": "lang",
                "decision": "UPDATE",
            },
        ],
    )

    monkeypatch.setattr(admin_api, "_get_memory_manager", lambda: _FakeMemoryManagerWithBackend(backend_dir))

    report = await admin_api.get_memory_extraction_quality_report(window_days=7)
    assert report["status"] == "ok"
    assert report["current_window"]["event_total"] == 2
    assert report["current_window"]["high_value_attempts"] == 3
    assert report["current_window"]["precision_proxy"] >= 0.0
    assert report["current_window"]["recall_proxy"] >= 0.0
    assert report["trend"]["quality_level"] in {"healthy", "warning", "critical"}

    export_path = tmp_path / "memory_quality.md"
    exported = await admin_api.export_memory_extraction_quality_report(
        {"window_days": 7, "output_path": str(export_path)}
    )
    assert exported["status"] == "ok"
    assert export_path.is_file()
    content = export_path.read_text(encoding="utf-8")
    assert "Memory Extraction Quality Report" in content
    assert "precision_proxy" in content


def _sample_trajectory_payload():
    return {
        "run_id": "traj_a",
        "meta": {"user_content": "抓取一个页面并总结"},
        "events": [
            {
                "ts": 100.0,
                "stage": "act",
                "action": "tool_call",
                "payload": {
                    "tool": "web_fetch",
                    "tool_call_id": "tc_1",
                    "args_hash": "abcd",
                    "args_preview": "{\"url\":\"https://example.com\"}",
                },
            },
            {
                "ts": 101.2,
                "stage": "act",
                "action": "tool_result",
                "payload": {
                    "tool": "web_fetch",
                    "tool_call_id": "tc_1",
                    "status": "error",
                    "error_code": "WEB_FETCH_FAILED",
                    "result_preview": "Error [WEB_FETCH_FAILED]: failed",
                },
            },
        ],
        "final": {"status": "llm_error", "final_content": "请求失败", "metrics": {"turn_latency_ms": 1288.0}},
    }


def test_trajectory_replay_helpers():
    payload = _sample_trajectory_payload()
    steps = admin_api._normalize_trajectory_steps(payload)
    assert len(steps) == 2
    assert steps[1]["error_code"] == "WEB_FETCH_FAILED"

    task_view = admin_api._build_task_view(payload)
    assert task_view["event_count"] == 2
    assert task_view["error_count"] == 1
    assert task_view["duration_ms"] == 1200.0

    baseline = _sample_trajectory_payload()
    baseline["events"][1]["payload"]["error_code"] = "TOOL_EXECUTION_FAILED"
    compare = admin_api._compare_replay_steps(steps, admin_api._normalize_trajectory_steps(baseline))
    assert compare["shared_steps"] == 1
    assert compare["overlap_ratio"] == 0.5


def test_trajectory_resume_helper():
    payload = _sample_trajectory_payload()
    resume = admin_api._build_resume_payload(payload)
    assert resume["can_resume"] is True
    assert resume["status"] == "llm_error"
    assert resume["last_error"]["error_code"] == "WEB_FETCH_FAILED"
    assert "继续上次任务" in resume["resume_message"]


@pytest.mark.asyncio
async def test_send_trajectory_resume_enqueues_chat_message(monkeypatch):
    payload = _sample_trajectory_payload()

    class _FakeStore:
        def get_trajectory(self, run_id):
            return payload if run_id == "traj_a" else None

    fake_q = _FakeInputQueue()
    fake_task_store = _FakeTaskRunStore()
    _patch_store(monkeypatch, _FakeStore())
    monkeypatch.setattr(admin_api, "API_QUEUES", {"input": fake_q, "output": None})
    monkeypatch.setattr(_admin_state, "API_QUEUES", {"input": fake_q, "output": None})
    monkeypatch.setattr(admin_api, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(_coding_helpers, "TASK_RUN_STORE", fake_task_store)
    res = await admin_api.send_trajectory_resume("traj_a", {"session_id": "web-main"})
    assert res["status"] == "enqueued"
    assert res["task_id"] == "task_1"
    assert res["chat_id"] == "web-main"
    assert len(fake_q.items) == 1
    queued = fake_q.items[0]
    assert queued["type"] == "chat"
    assert queued["source"] == "resume_send"
    assert queued["chat_id"] == "web-main"
    assert "继续上次任务" in queued["content"]


@pytest.mark.asyncio
async def test_replay_execute_creates_task_and_enqueues(monkeypatch):
    payload = _sample_trajectory_payload()

    class _FakeStore:
        def get_trajectory(self, run_id):
            if run_id in {"traj_a", "traj_b"}:
                return payload
            return None

    fake_q = _FakeInputQueue()
    fake_task_store = _FakeTaskRunStore()
    _patch_store(monkeypatch, _FakeStore())
    monkeypatch.setattr(admin_api, "API_QUEUES", {"input": fake_q, "output": None})
    monkeypatch.setattr(_admin_state, "API_QUEUES", {"input": fake_q, "output": None})
    monkeypatch.setattr(admin_api, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(_coding_helpers, "TASK_RUN_STORE", fake_task_store)
    res = await admin_api.replay_execute_trajectory(
        "traj_a",
        {"session_id": "web-main", "compare_run_id": "traj_b"},
    )
    assert res["status"] == "enqueued"
    assert res["task_id"] == "task_1"
    assert len(fake_q.items) == 1
    assert fake_q.items[0]["source"] == "replay_execute"


@pytest.mark.asyncio
async def test_task_run_endpoints_and_coding_loop(monkeypatch):
    fake_task_store = _FakeTaskRunStore()
    fake_q = _FakeInputQueue()
    task = fake_task_store.create(kind="resume_send", run_id="traj_a", session_id="web-main")
    monkeypatch.setattr(admin_api, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(_coding_helpers, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(admin_api, "API_QUEUES", {"input": fake_q, "output": None})
    monkeypatch.setattr(_admin_state, "API_QUEUES", {"input": fake_q, "output": None})
    listed = await admin_api.list_task_runs(limit=20, status=None, kind=None)
    assert listed["status"] == "ok"
    assert listed["total"] >= 1

    got = await admin_api.get_task_run(task["task_id"])
    assert got["status"] == "ok"
    assert got["task"]["task_id"] == task["task_id"]

    loop_res = await admin_api.run_task_coding_loop(task["task_id"], {"session_id": "web-main"})
    assert loop_res["status"] == "enqueued"
    assert len(fake_q.items) == 1
    assert fake_q.items[0]["source"] == "coding_loop"


@pytest.mark.asyncio
async def test_task_run_coding_loop_deterministic_success(monkeypatch, tmp_path: Path):
    fake_task_store = _FakeTaskRunStore()
    task = fake_task_store.create(kind="resume_send", run_id="traj_a", session_id="web-main")
    monkeypatch.setattr(admin_api, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(_coding_helpers, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(admin_api, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_coding_helpers, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_admin_state, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_workflow_helpers, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        admin_api,
        "_run_verify_command",
        lambda command, cwd, timeout_seconds: {"command": command, "ok": True, "returncode": 0},
    )
    admin_api._coding_quality_history.clear()

    target = tmp_path / "demo.txt"
    target.write_text("hello world", encoding="utf-8")
    payload = {
        "mode": "deterministic",
        "goal": "replace token",
        "edits": [{"file": "demo.txt", "find": "world", "replace": "gazer"}],
        "test_commands": ["pytest -q"],
    }
    res = await admin_api.run_task_coding_loop(task["task_id"], payload)
    assert res["status"] == "completed"
    assert res["mode"] == "deterministic"
    assert "demo.txt" in res["output"]["files_changed"]
    assert target.read_text(encoding="utf-8") == "hello gazer"


@pytest.mark.asyncio
async def test_task_run_coding_loop_deterministic_failure_rolls_back(monkeypatch, tmp_path: Path):
    fake_task_store = _FakeTaskRunStore()
    task = fake_task_store.create(kind="resume_send", run_id="traj_a", session_id="web-main")
    monkeypatch.setattr(admin_api, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(_coding_helpers, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(admin_api, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_coding_helpers, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_admin_state, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_workflow_helpers, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        _coding_helpers,
        "_run_verify_command",
        lambda command, cwd, timeout_seconds: {"command": command, "ok": False, "returncode": 1},
    )

    target = tmp_path / "demo.txt"
    target.write_text("hello world", encoding="utf-8")
    payload = {
        "mode": "deterministic",
        "goal": "replace token",
        "max_retries": 0,
        "edits": [{"file": "demo.txt", "find": "world", "replace": "gazer"}],
        "test_commands": ["pytest -q"],
    }
    with pytest.raises(admin_api.HTTPException) as exc:
        await admin_api.run_task_coding_loop(task["task_id"], payload)
    assert exc.value.status_code == 400
    assert target.read_text(encoding="utf-8") == "hello world"


@pytest.mark.asyncio
async def test_get_coding_quality_metrics(monkeypatch):
    admin_api._coding_quality_history.clear()
    admin_api._record_coding_quality_event(
        {
            "task_id": "t1",
            "run_id": "r1",
            "success": True,
            "duration_ms": 123.0,
            "files_changed": 2,
            "tests_total": 1,
            "tests_passed": 1,
        }
    )
    payload = await admin_api.get_coding_quality(window=10)
    assert payload["status"] == "ok"
    assert payload["metrics"]["total_runs"] == 1
    assert payload["metrics"]["pass_rate"] == 1.0


@pytest.mark.asyncio
async def test_get_coding_quality_metrics_filter_by_kind(monkeypatch):
    admin_api._coding_quality_history.clear()
    admin_api._record_coding_quality_event(
        {"task_id": "t1", "run_id": "r1", "kind": "coding_loop", "success": True, "duration_ms": 50.0}
    )
    admin_api._record_coding_quality_event(
        {"task_id": "t2", "run_id": "r2", "kind": "replay_execute", "success": False, "duration_ms": 80.0}
    )
    payload = await admin_api.get_coding_quality(window=10, kind="coding_loop")
    assert payload["status"] == "ok"
    assert payload["metrics"]["total_runs"] == 1
    assert payload["metrics"]["success_runs"] == 1


@pytest.mark.asyncio
async def test_task_run_coding_loop_deterministic_supports_advanced_operations(monkeypatch, tmp_path: Path):
    fake_task_store = _FakeTaskRunStore()
    task = fake_task_store.create(kind="resume_send", run_id="traj_ops", session_id="web-main")
    monkeypatch.setattr(admin_api, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(_coding_helpers, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(admin_api, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_coding_helpers, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_admin_state, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_workflow_helpers, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        admin_api,
        "_run_verify_command",
        lambda command, cwd, timeout_seconds: {"command": command, "ok": True, "returncode": 0},
    )

    target = tmp_path / "ops.txt"
    target.write_text("A\nhello world\nB", encoding="utf-8")
    payload = {
        "mode": "deterministic",
        "goal": "apply operation set",
        "edits": [
            {"file": "ops.txt", "operation": "insert_before", "anchor": "hello world", "replace": "P\n"},
            {"file": "ops.txt", "operation": "regex_replace", "find": r"hello\s+world", "replace": "hi gazer"},
            {"file": "ops.txt", "operation": "insert_after", "anchor": "hi gazer", "replace": "\nS"},
            {"file": "ops.txt", "operation": "delete", "find": "A\n"},
        ],
    }

    res = await admin_api.run_task_coding_loop(task["task_id"], payload)
    assert res["status"] == "completed"
    output_text = target.read_text(encoding="utf-8")
    assert output_text == "P\nhi gazer\nS\nB"


@pytest.mark.asyncio
async def test_task_run_coding_loop_deterministic_atomic_apply(monkeypatch, tmp_path: Path):
    fake_task_store = _FakeTaskRunStore()
    task = fake_task_store.create(kind="resume_send", run_id="traj_atomic", session_id="web-main")
    monkeypatch.setattr(admin_api, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(_coding_helpers, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(admin_api, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_coding_helpers, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_admin_state, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_workflow_helpers, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        admin_api,
        "_run_verify_command",
        lambda command, cwd, timeout_seconds: {"command": command, "ok": True, "returncode": 0},
    )

    target = tmp_path / "atomic.txt"
    original = "hello world"
    target.write_text(original, encoding="utf-8")
    payload = {
        "mode": "deterministic",
        "goal": "should fail as a whole",
        "edits": [
            {"file": "atomic.txt", "find": "world", "replace": "gazer"},
            {"file": "atomic.txt", "find": "MISSING_TOKEN", "replace": "x"},
        ],
    }

    with pytest.raises(admin_api.HTTPException) as exc:
        await admin_api.run_task_coding_loop(task["task_id"], payload)
    assert exc.value.status_code == 400
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_task_run_coding_loop_records_recovery_count(monkeypatch, tmp_path: Path):
    fake_task_store = _FakeTaskRunStore()
    task = fake_task_store.create(kind="resume_send", run_id="traj_retry", session_id="web-main")
    monkeypatch.setattr(admin_api, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(_coding_helpers, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(admin_api, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_coding_helpers, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_admin_state, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_workflow_helpers, "_PROJECT_ROOT", tmp_path)
    calls = {"n": 0}

    def _flaky_verify(command, cwd, timeout_seconds):
        calls["n"] += 1
        return {"command": command, "ok": calls["n"] >= 2, "returncode": 0 if calls["n"] >= 2 else 1}

    monkeypatch.setattr(_coding_helpers, "_run_verify_command", _flaky_verify)
    admin_api._coding_quality_history.clear()

    target = tmp_path / "retry.txt"
    target.write_text("hello world", encoding="utf-8")
    payload = {
        "mode": "deterministic",
        "goal": "retry once then pass",
        "max_retries": 1,
        "edits": [{"file": "retry.txt", "find": "world", "replace": "gazer"}],
        "test_commands": ["pytest -q"],
    }
    res = await admin_api.run_task_coding_loop(task["task_id"], payload)
    assert res["status"] == "completed"
    assert res["output"]["recovery_count"] == 1

    quality = await admin_api.get_coding_quality(window=10)
    assert quality["metrics"]["conflict_recoveries"] >= 1


@pytest.mark.asyncio
async def test_coding_benchmark_run_and_history_and_leaderboard(monkeypatch):
    admin_api._coding_benchmark_history.clear()

    payload = {
        "name": "suite_a",
        "cases": [
            {
                "id": "ok_case",
                "goal": "replace token",
                "files": {"demo.txt": "hello world"},
                "edits": [{"file": "demo.txt", "find": "world", "replace": "gazer"}],
                "verify_contains": {"demo.txt": "hello gazer"},
            },
            {
                "id": "fail_case",
                "goal": "missing",
                "files": {"demo.txt": "hello world"},
                "edits": [{"file": "demo.txt", "find": "NOT_FOUND", "replace": "x"}],
            },
        ],
    }
    run = await admin_api.run_coding_benchmark(payload)
    assert run["status"] == "ok"
    assert run["summary"]["total_cases"] == 2
    assert run["summary"]["success_cases"] == 1

    hist = await admin_api.get_coding_benchmark_history(limit=10)
    assert hist["status"] == "ok"
    assert hist["total"] >= 1

    lb = await admin_api.get_coding_benchmark_leaderboard(window=10)
    assert lb["status"] == "ok"
    assert lb["leaderboard"]["total_runs"] >= 1
    assert lb["leaderboard"]["top"][0]["name"] == "suite_a"


def test_maybe_run_scheduled_coding_benchmark_disabled(monkeypatch):
    _patch_config(monkeypatch, _FakeConfig({"security": {"coding_benchmark_scheduler": {"enabled": False}}}))
    res = admin_api._maybe_run_scheduled_coding_benchmark()
    assert res["ran"] is False
    assert res["reason"] == "disabled"


def test_maybe_run_scheduled_coding_benchmark_due_and_interval(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "security": {
                "coding_benchmark_scheduler": {
                    "enabled": True,
                    "interval_seconds": 3600,
                    "auto_link_release_gate": False,
                    "payload": {
                        "name": "sched_suite",
                        "cases": [{"id": "c1"}],
                    },
                }
            }
        }
    )
    _patch_config(monkeypatch, fake_cfg)
    monkeypatch.setattr(_coding_helpers, "_run_coding_benchmark_suite", lambda payload: {"name": payload.get("name"), "score": 1.0})
    admin_api._coding_benchmark_scheduler_state["last_run_ts"] = 0.0

    ran = admin_api._maybe_run_scheduled_coding_benchmark()
    assert ran["ran"] is True
    assert ran["summary"]["name"] == "sched_suite"

    skipped = admin_api._maybe_run_scheduled_coding_benchmark()
    assert skipped["ran"] is False
    assert skipped["reason"] == "not_due"


@pytest.mark.asyncio
async def test_coding_benchmark_scheduler_endpoints(monkeypatch):
    fake_cfg = _FakeConfig(
        {
            "security": {
                "coding_benchmark_scheduler": {
                    "enabled": True,
                    "interval_seconds": 1800,
                    "auto_link_release_gate": False,
                    "payload": {"name": "sched_suite", "cases": [{"id": "c1"}]},
                }
            }
        }
    )
    _patch_config(monkeypatch, fake_cfg)
    monkeypatch.setattr(_debug, "_maybe_run_scheduled_coding_benchmark", lambda force=False: {"ran": bool(force), "summary": {"name": "sched_suite"}})

    status = await admin_api.get_coding_benchmark_scheduler_status()
    assert status["status"] == "ok"
    assert status["scheduler"]["enabled"] is True

    run_now = await admin_api.run_coding_benchmark_scheduler_now()
    assert run_now["status"] == "ok"
    assert run_now["result"]["ran"] is True


@pytest.mark.asyncio
async def test_coding_benchmark_observability_and_export(monkeypatch):
    admin_api._coding_benchmark_history.clear()
    admin_api._coding_benchmark_history.append(
        {
            "name": "suite_obs_1",
            "score": 0.5,
            "success_cases": 1,
            "total_cases": 2,
            "duration_ms": 1200,
            "ts": 1700000000.0,
            "cases": [
                {"id": "c1", "success": False, "contains_errors": ["edits[0] not found"]},
                {"id": "c2", "success": True, "contains_errors": []},
            ],
        }
    )
    admin_api._coding_benchmark_history.append(
        {
            "name": "suite_obs_2",
            "score": 1.0,
            "success_cases": 2,
            "total_cases": 2,
            "duration_ms": 900,
            "ts": 1700000100.0,
            "cases": [
                {"id": "c3", "success": True, "contains_errors": []},
            ],
        }
    )
    payload = await admin_api.get_coding_benchmark_observability(window=60)
    assert payload["status"] == "ok"
    assert payload["observability"]["total_runs"] >= 2
    assert isinstance(payload["observability"]["trend"], list)
    assert isinstance(payload["observability"]["failure_reasons"], list)

    csv_res = await admin_api.export_coding_benchmark_csv(window=60)
    text = csv_res.body.decode("utf-8")
    assert "suite_obs_1" in text
    assert "score" in text


def test_assess_coding_benchmark_health_levels(monkeypatch):
    admin_api._coding_benchmark_history.clear()
    healthy = admin_api._assess_coding_benchmark_health(window=20)
    assert healthy["level"] == "unknown"

    admin_api._coding_benchmark_history.append({"name": "a", "score": 0.95, "success_cases": 9, "total_cases": 10})
    res = admin_api._assess_coding_benchmark_health(window=20)
    assert res["level"] == "healthy"
    assert res["recommend_block_high_risk"] is False

    admin_api._coding_benchmark_history.clear()
    admin_api._coding_benchmark_history.append({"name": "b", "score": 0.4, "success_cases": 2, "total_cases": 10})
    critical = admin_api._assess_coding_benchmark_health(window=20)
    assert critical["level"] == "critical"
    assert critical["recommend_block_high_risk"] is True


@pytest.mark.asyncio
async def test_auto_link_coding_benchmark_release_gate_endpoint(monkeypatch):
    class _FakeEvalForCodingBench:
        def __init__(self):
            self.gate = {"blocked": False, "reason": "", "source": "manual"}
            self.tasks = [{"task_id": "cb_1", "status": "open", "dataset_id": "coding_benchmark_auto"}]

        def get_release_gate_status(self):
            return dict(self.gate)

        def set_release_gate_status(self, **kwargs):
            self.gate = {
                "blocked": bool(kwargs.get("blocked", False)),
                "reason": kwargs.get("reason", ""),
                "source": kwargs.get("source", ""),
            }
            return dict(self.gate)

        def register_gate_result(self, **kwargs):
            return {"task_created": True, "fail_streak": 2}

        def list_optimization_tasks(self, limit=50, status=None, dataset_id=None):
            items = list(self.tasks)
            if status:
                items = [item for item in items if item["status"] == status]
            if dataset_id:
                items = [item for item in items if item["dataset_id"] == dataset_id]
            return items[:limit]

        def set_optimization_task_status(self, task_id, status, note=""):
            for item in self.tasks:
                if item["task_id"] == task_id:
                    item["status"] = status
                    return item
            return None

    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _FakeEvalForCodingBench())
    monkeypatch.setattr(_training_helpers, "_get_eval_benchmark_manager", lambda: _FakeEvalForCodingBench())
    admin_api._coding_benchmark_history.clear()
    admin_api._coding_benchmark_history.append({"name": "s1", "score": 0.3, "success_cases": 1, "total_cases": 10})

    payload = await admin_api.auto_link_coding_benchmark_release_gate({"window": 10})
    assert payload["status"] == "ok"
    assert payload["actions"]["changed_gate"] is True
    assert payload["gate"]["blocked"] is True
    assert payload["health"]["level"] == "critical"


@pytest.mark.asyncio
async def test_run_coding_benchmark_with_auto_link(monkeypatch):
    class _FakeEvalForRun:
        def __init__(self):
            self.gate = {"blocked": False, "reason": "", "source": "manual"}

        def get_release_gate_status(self):
            return dict(self.gate)

        def set_release_gate_status(self, **kwargs):
            self.gate = {
                "blocked": bool(kwargs.get("blocked", False)),
                "reason": kwargs.get("reason", ""),
                "source": kwargs.get("source", ""),
            }
            return dict(self.gate)

        def register_gate_result(self, **kwargs):
            return {"task_created": True, "fail_streak": 1}

        def list_optimization_tasks(self, limit=50, status=None, dataset_id=None):
            return []

        def set_optimization_task_status(self, task_id, status, note=""):
            return None

    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _FakeEvalForRun())
    monkeypatch.setattr(_training_helpers, "_get_eval_benchmark_manager", lambda: _FakeEvalForRun())
    admin_api._coding_benchmark_history.clear()

    run = await admin_api.run_coding_benchmark(
        {
            "name": "suite_auto_link",
            "auto_link_release_gate": True,
            "cases": [
                {
                    "id": "fail_case",
                    "files": {"a.txt": "hello"},
                    "edits": [{"file": "a.txt", "find": "MISSING", "replace": "x"}],
                }
            ],
        }
    )
    assert run["status"] == "ok"
    assert isinstance(run["auto_link"], dict)
    assert run["health"] is not None


@pytest.mark.asyncio
async def test_auto_resume_trajectory_sets_auto_mode(monkeypatch):
    payload = _sample_trajectory_payload()

    class _FakeStore:
        def get_trajectory(self, run_id):
            return payload if run_id == "traj_a" else None

    fake_q = _FakeInputQueue()
    fake_task_store = _FakeTaskRunStore()
    _patch_store(monkeypatch, _FakeStore())
    monkeypatch.setattr(admin_api, "API_QUEUES", {"input": fake_q, "output": None})
    monkeypatch.setattr(_admin_state, "API_QUEUES", {"input": fake_q, "output": None})
    monkeypatch.setattr(admin_api, "TASK_RUN_STORE", fake_task_store)
    monkeypatch.setattr(_coding_helpers, "TASK_RUN_STORE", fake_task_store)
    res = await admin_api.auto_resume_trajectory("traj_a", {"session_id": "web-main"})
    assert res["status"] == "enqueued"
    assert res["mode"] == "auto"
    assert len(fake_q.items) == 1
    assert fake_q.items[0]["source"] == "resume_send"


@pytest.mark.asyncio
async def test_task_run_coding_loop_not_found(monkeypatch):
    monkeypatch.setattr(admin_api, "TASK_RUN_STORE", _FakeTaskRunStore())
    monkeypatch.setattr(_coding_helpers, "TASK_RUN_STORE", _FakeTaskRunStore())
    monkeypatch.setattr(admin_api, "API_QUEUES", {"input": _FakeInputQueue(), "output": None})
    monkeypatch.setattr(_admin_state, "API_QUEUES", {"input": _FakeInputQueue(), "output": None})
    with pytest.raises(admin_api.HTTPException) as exc:
        await admin_api.run_task_coding_loop("missing_task", {"session_id": "web-main"})
    assert exc.value.status_code == 404


def test_auto_link_release_gate_blocks_and_recovers():
    class _FakeEval:
        def __init__(self):
            self.gate = {"blocked": False}
            self.tasks = [{"task_id": "x1", "status": "open", "dataset_id": "workflow_health_auto"}]
            self.last_register = None

        def set_release_gate_status(self, **kwargs):
            self.gate = {
                "blocked": bool(kwargs.get("blocked", False)),
                "reason": kwargs.get("reason", ""),
                "source": kwargs.get("source", ""),
            }
            return self.gate

        def register_gate_result(self, **kwargs):
            self.last_register = kwargs
            return {"task_created": True, "fail_streak": 1, "task": {"task_id": "auto_1"}}

        def list_optimization_tasks(self, limit=50, status=None, dataset_id=None):
            out = list(self.tasks)
            if status:
                out = [x for x in out if x["status"] == status]
            if dataset_id:
                out = [x for x in out if x["dataset_id"] == dataset_id]
            return out[:limit]

        def set_optimization_task_status(self, task_id, status, note=""):
            for item in self.tasks:
                if item["task_id"] == task_id:
                    item["status"] = status
                    return item
            return None

    mgr = _FakeEval()
    blocked_actions = admin_api._auto_link_release_gate(
        manager=mgr,
        gate={"blocked": False},
        health={"recommend_block_high_risk": True, "level": "critical", "message": "critical"},
    )
    assert blocked_actions["created_task"] is True
    assert mgr.gate["blocked"] is True

    recovered_actions = admin_api._auto_link_release_gate(
        manager=mgr,
        gate={"blocked": True},
        health={"recommend_block_high_risk": False, "level": "healthy", "message": "ok"},
    )
    assert recovered_actions["resolved_tasks"] >= 1
    assert mgr.gate["blocked"] is False


@pytest.mark.asyncio
async def test_auto_link_release_gate_endpoint(monkeypatch):
    class _FakeEvalForEndpoint:
        def __init__(self):
            self.gate = {"blocked": False, "reason": "", "source": "manual"}
            self.tasks = [{"task_id": "t1", "status": "open", "dataset_id": "workflow_health_auto"}]

        def get_release_gate_status(self):
            return dict(self.gate)

        def set_release_gate_status(self, **kwargs):
            self.gate = {
                "blocked": bool(kwargs.get("blocked", False)),
                "reason": kwargs.get("reason", ""),
                "source": kwargs.get("source", ""),
            }
            return dict(self.gate)

        def register_gate_result(self, **kwargs):
            return {"task_created": True, "fail_streak": 2}

        def list_optimization_tasks(self, limit=50, status=None, dataset_id=None):
            items = list(self.tasks)
            if status:
                items = [item for item in items if item["status"] == status]
            if dataset_id:
                items = [item for item in items if item["dataset_id"] == dataset_id]
            return items[:limit]

        def set_optimization_task_status(self, task_id, status, note=""):
            for item in self.tasks:
                if item["task_id"] == task_id:
                    item["status"] = status
                    return item
            return None

    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _FakeEvalForEndpoint())
    monkeypatch.setattr(_training_helpers, "_get_eval_benchmark_manager", lambda: _FakeEvalForEndpoint())
    monkeypatch.setattr(admin_api, "_build_workflow_observability_metrics", lambda limit=200: {"total_runs": 0})
    monkeypatch.setattr(admin_api, "_latest_persona_consistency_signal", lambda: {})
    monkeypatch.setattr(
        admin_api,
        "_assess_release_gate_workflow_health",
        lambda **kwargs: {"recommend_block_high_risk": True, "level": "critical", "message": "critical"},
    )

    payload = await admin_api.auto_link_release_gate()
    assert payload["status"] == "ok"
    assert payload["actions"]["changed_gate"] is True
    assert payload["gate"]["blocked"] is True
