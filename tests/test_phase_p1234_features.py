import asyncio
import hashlib
import hmac
import json
from pathlib import Path

import pytest
import yaml

from bus.queue import MessageBus
from channels.discord import DiscordChannel
from eval.persona_consistency import PersonaConsistencyManager
from eval.trainer import TrainingJobManager
from plugins.loader import PluginLoader
from security.pairing import pairing_manager
from tools.admin import api_facade as admin_api


class _FakeConfig:
    def __init__(self, data):
        self.data = data
        self.saved = 0

    def get(self, key_path, default=None):
        cur = self.data
        for part in str(key_path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set_many(self, updates):
        for key, value in updates.items():
            parts = str(key).split(".")
            cur = self.data
            for part in parts[:-1]:
                if part not in cur or not isinstance(cur[part], dict):
                    cur[part] = {}
                cur = cur[part]
            cur[parts[-1]] = value

    def save(self):
        self.saved += 1


def _build_signed_plugin(plugin_dir: Path, *, secret: str, key_id: str = "dev-key") -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    plugin_py = plugin_dir / "plugin.py"
    plugin_py.write_text("def setup(api):\n    return None\n", encoding="utf-8")
    digest = hashlib.sha256(plugin_py.read_bytes()).hexdigest()
    payload = {
        "id": "demo-plugin",
        "name": "Demo Plugin",
        "version": "0.1.0",
        "slot": "tool",
        "entry": "plugin:setup",
        "integrity": {"plugin.py": digest},
    }
    signed_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    manifest = dict(payload)
    manifest["signature"] = {"key_id": key_id, "value": signature}
    (plugin_dir / "gazer_plugin.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def test_plugin_loader_signature_and_integrity(monkeypatch, tmp_path):
    extensions_dir = tmp_path / "extensions" / "demo-plugin"
    secret = "s3cr3t"
    _build_signed_plugin(extensions_dir, secret=secret)
    monkeypatch.setattr(
        "plugins.loader.gazer_config",
        _FakeConfig(
            {
                "plugins": {
                    "signature": {
                        "enforce": True,
                        "allow_unsigned": False,
                        "trusted_keys": {"dev-key": secret},
                    }
                }
            }
        ),
    )
    loader = PluginLoader(workspace=tmp_path, search_dirs=[tmp_path / "extensions"])
    manifests = loader.discover()
    assert "demo-plugin" in manifests
    assert manifests["demo-plugin"].verification_error == ""

    (extensions_dir / "plugin.py").write_text("def setup(api):\n    return 42\n", encoding="utf-8")
    manifests2 = loader.discover()
    assert "demo-plugin" not in manifests2
    assert "demo-plugin" in loader.failed_ids


@pytest.mark.asyncio
async def test_discord_channel_ingest_routes_to_message_bus():
    bus = MessageBus()
    channel = DiscordChannel(token="", allowed_guild_ids=["g1"])
    channel.bind(bus)
    pairing_manager.add_approved("discord", "u1")
    await channel.ingest_message(content="hello", chat_id="c1", sender_id="u1", guild_id="g1")
    message = await asyncio.wait_for(bus.inbound.get(), timeout=1.0)
    assert message.channel == "discord"
    assert message.chat_id == "c1"
    assert message.sender_id == "u1"
    assert message.content == "hello"


def test_training_experiment_compare(tmp_path):
    manager = TrainingJobManager(base_dir=tmp_path / "eval")
    sample = manager.create_sample_store(
        dataset_id="ds1",
        trajectory_samples=[{"assistant_output": "ok", "feedback": "unsafe tool wrong"}],
        eval_samples=[{"passed": False}],
        source="test",
    )
    exp = manager.create_experiment(dataset_id="ds1", name="exp1", sample_store_id=sample["store_id"])
    job = manager.create_job(
        dataset_id="ds1",
        trajectory_samples=sample["trajectory_samples"],
        eval_samples=sample["eval_samples"],
        source="experiment",
        metadata={"experiment_id": exp["experiment_id"]},
    )
    done = manager.run_job(job["job_id"])
    assert done is not None and done["status"] == "completed"
    manager.append_experiment_run(
        experiment_id=exp["experiment_id"],
        job_id=job["job_id"],
        metrics={"score": 0.7, "fail_count": 1, "rule_count": 2},
    )
    compare = manager.compare_experiment_runs(exp["experiment_id"])
    assert compare is not None
    assert compare["run_count"] == 1
    assert compare["best_job_id"] == job["job_id"]


@pytest.mark.asyncio
async def test_persona_mental_process_and_release_gate_signal(monkeypatch, tmp_path):
    fake_cfg = _FakeConfig(
        {
            "personality": {
                "mental_process": {
                    "initial_state": "IDLE",
                    "states": [{"name": "IDLE", "description": "idle"}],
                    "on_input_transition": {"IDLE": "IDLE"},
                }
            },
            "observability": {
                "release_gate_health_thresholds": {
                    "warning_persona_consistency_score": 0.85,
                    "critical_persona_consistency_score": 0.7,
                }
            },
        }
    )
    monkeypatch.setattr(admin_api, "config", fake_cfg)

    persona_manager = PersonaConsistencyManager(base_dir=tmp_path / "persona")
    dataset = persona_manager.build_dataset(name="core", system_prompt="You are Gazer.")
    persona_manager.run_dataset(
        dataset["id"],
        outputs={
            "tone_warm": "ok",
            "identity_consistency": "I am Gazer.",
            "safety_consistency": "safer way",
        },
    )
    monkeypatch.setattr(admin_api, "_get_persona_eval_manager", lambda: persona_manager)

    class _FakeEval:
        def get_release_gate_status(self):
            return {"blocked": False, "reason": "quality_gate_passed", "source": "eval:test"}

    monkeypatch.setattr(admin_api, "_get_eval_benchmark_manager", lambda: _FakeEval())
    monkeypatch.setattr(admin_api, "_build_workflow_observability_metrics", lambda limit=200: {"total_runs": 1, "failures": 0, "success_rate": 1.0, "p95_latency_ms": 500})

    yaml_body = """
initial_state: IDLE
states:
  - name: IDLE
    description: keep calm
on_input_transition:
  IDLE: IDLE
"""
    saved = await admin_api.update_persona_mental_process({"yaml": yaml_body})
    assert saved["status"] == "ok"
    gate = await admin_api.get_release_gate_status()
    assert gate["status"] == "ok"
    assert "persona" in gate
    assert "health" in gate


@pytest.mark.asyncio
async def test_observability_trends_endpoint():
    admin_api._append_alert("warning", "test", "demo", {"x": 1})
    payload = await admin_api.get_observability_trends(window=20)
    assert payload["status"] == "ok"
    assert "trends" in payload
