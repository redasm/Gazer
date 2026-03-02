"""Tests for soul.evolution -- GazerEvolution."""

import os
import json
import pytest
import soul.evolution.gazer_evolution as evolution_mod
from soul.evolution import GazerEvolution


@pytest.fixture
def evo(tmp_dir):
    path = str(tmp_dir / "feedback.json")
    return GazerEvolution(feedback_path=path)


class TestCollectFeedback:
    def test_basic_collect(self, evo):
        evo.collect_feedback("positive", "telegram_reply", "Great answer!")
        feedbacks = evo._load_feedback()
        assert len(feedbacks) == 1
        assert feedbacks[0]["label"] == "positive"
        assert feedbacks[0]["context"] == "telegram_reply"
        assert feedbacks[0]["feedback"] == "Great answer!"

    def test_multiple_feedbacks(self, evo):
        evo.collect_feedback("positive", "web", "good")
        evo.collect_feedback("negative", "web", "bad")
        evo.collect_feedback("correction", "telegram", "fix this")
        feedbacks = evo._load_feedback()
        assert len(feedbacks) == 3

    def test_feedback_has_timestamp(self, evo):
        evo.collect_feedback("positive", "test")
        fb = evo._load_feedback()[0]
        assert "timestamp" in fb
        assert "T" in fb["timestamp"]  # ISO format

    def test_empty_feedback_text(self, evo):
        evo.collect_feedback("negative", "ctx")
        fb = evo._load_feedback()[0]
        assert fb["feedback"] == ""


class TestGetFeedbackStats:
    def test_empty_stats(self, evo):
        stats = evo.get_feedback_stats()
        assert stats["total"] == 0
        assert stats["positive"] == 0
        assert stats["negative"] == 0
        assert stats["correction"] == 0

    def test_stats_after_collection(self, evo):
        evo.collect_feedback("positive", "a")
        evo.collect_feedback("positive", "b")
        evo.collect_feedback("negative", "c")
        evo.collect_feedback("correction", "d")
        stats = evo.get_feedback_stats()
        assert stats["total"] == 4
        assert stats["positive"] == 2
        assert stats["negative"] == 1
        assert stats["correction"] == 1


class TestSummarizeFeedback:
    def test_with_text(self, evo):
        feedbacks = [
            {"label": "negative", "context": "web", "feedback": "Too verbose"},
            {"label": "correction", "context": "tg", "feedback": "Wrong date"},
        ]
        summary = evo._summarize_feedback(feedbacks)
        assert "negative" in summary
        assert "Too verbose" in summary
        assert "Wrong date" in summary

    def test_without_text(self, evo):
        feedbacks = [
            {"label": "positive", "context": "web", "feedback": ""},
        ]
        summary = evo._summarize_feedback(feedbacks)
        assert "positive" in summary
        assert "web" in summary


class TestPersistence:
    def test_load_empty(self, evo):
        assert evo._load_feedback() == []

    def test_save_and_load(self, evo):
        data = [{"label": "positive", "context": "test", "feedback": "ok", "timestamp": "2026-02-05T10:00:00"}]
        evo._save_feedback(data)
        loaded = evo._load_feedback()
        assert loaded == data

    def test_load_corrupt_file(self, evo):
        with open(evo.feedback_path, "w") as f:
            f.write("not json")
        assert evo._load_feedback() == []


class TestArchiveFeedback:
    def test_archive_creates_file(self, evo, tmp_dir):
        feedbacks = [{"label": "positive", "context": "a", "feedback": "", "timestamp": "2026-02-05T10:00:00"}]
        evo._save_feedback(feedbacks)
        evo._archive_feedback(feedbacks)

        # Active feedback should be cleared
        assert evo._load_feedback() == []

        # Archive file should exist
        archive_dir = os.path.join(os.path.dirname(evo.feedback_path), "feedback_archive")
        assert os.path.exists(archive_dir)
        files = os.listdir(archive_dir)
        assert len(files) == 1
        with open(os.path.join(archive_dir, files[0]), "r") as f:
            archived = json.load(f)
        assert len(archived) == 1


class TestOptimizePersona:
    @pytest.mark.asyncio
    async def test_skip_no_feedback(self, evo):
        result = await evo.optimize_persona()
        assert result is False

    @pytest.mark.asyncio
    async def test_skip_insufficient_feedback(self, evo):
        evo.collect_feedback("positive", "a")
        evo.collect_feedback("positive", "b")
        result = await evo.optimize_persona()
        assert result is False


class _ConfigStub:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        current = self.data
        for key in str(key_path).split("."):
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current


class _AutoEvo(GazerEvolution):
    def __init__(self, feedback_path: str):
        super().__init__(feedback_path=feedback_path)
        self.optimize_calls = 0
        self.optimize_return = True

    async def optimize_persona(self) -> bool:
        self.optimize_calls += 1
        return bool(self.optimize_return)


def test_publish_gate_blocks_missing_personality_name(tmp_dir, monkeypatch):
    evo = GazerEvolution(feedback_path=str(tmp_dir / "feedback.json"))
    monkeypatch.setattr(
        evolution_mod,
        "config",
        _ConfigStub(
            {
                "personality": {
                    "name": "Gazer",
                    "evolution": {
                        "publish_gate": {
                            "enabled": True,
                            "min_similarity": 0.0,
                            "min_length_ratio": 0.1,
                            "max_length_ratio": 10.0,
                            "require_personality_name": True,
                        }
                    },
                }
            }
        ),
    )
    result = evo._evaluate_publish_gate("You are Gazer.", "You are an assistant.")
    assert result["passed"] is False
    assert result["reason"] == "missing_personality_name"


def test_publish_gate_disabled_allows_candidate(tmp_dir, monkeypatch):
    evo = GazerEvolution(feedback_path=str(tmp_dir / "feedback.json"))
    monkeypatch.setattr(
        evolution_mod,
        "config",
        _ConfigStub(
            {
                "personality": {
                    "evolution": {
                        "publish_gate": {
                            "enabled": False,
                        }
                    },
                }
            }
        ),
    )
    result = evo._evaluate_publish_gate("old", "")
    assert result["passed"] is True
    assert result["reason"] == "gate_disabled"


def test_publish_gate_respects_release_gate_block(tmp_dir, monkeypatch):
    evo = GazerEvolution(feedback_path=str(tmp_dir / "feedback.json"))

    class _EvalStub:
        def get_release_gate_status(self):
            return {"blocked": True, "reason": "quality_gate_blocked"}

    monkeypatch.setattr(evolution_mod, "EvalBenchmarkManager", lambda: _EvalStub())
    monkeypatch.setattr(
        evolution_mod,
        "config",
        _ConfigStub(
            {
                "personality": {
                    "name": "Gazer",
                    "evolution": {
                        "publish_gate": {
                            "enabled": True,
                            "respect_release_gate": True,
                            "min_similarity": 0.0,
                            "min_length_ratio": 0.1,
                            "max_length_ratio": 10.0,
                            "require_personality_name": False,
                        }
                    },
                }
            }
        ),
    )
    result = evo._evaluate_publish_gate("You are Gazer.", "You are Gazer, helpful.")
    assert result["passed"] is False
    assert result["reason"] == "release_gate_blocked"


def test_pre_publish_eval_blocks_low_score_and_sets_gate(tmp_dir, monkeypatch):
    evo = GazerEvolution(feedback_path=str(tmp_dir / "feedback.json"))
    calls = {"set_gate": 0}

    class _EvalStub:
        def get_release_gate_status(self):
            return {"blocked": False}

        def set_release_gate_status(self, **kwargs):
            calls["set_gate"] += 1
            return kwargs

    monkeypatch.setattr(evolution_mod, "EvalBenchmarkManager", lambda: _EvalStub())
    monkeypatch.setattr(
        evolution_mod,
        "config",
        _ConfigStub(
            {
                "personality": {
                    "evolution": {
                        "pre_publish_eval": {
                            "enabled": True,
                            "min_score": 0.95,
                            "block_on_fail": True,
                            "set_release_gate_on_fail": True,
                        }
                    }
                }
            }
        ),
    )
    payload = evo._evaluate_pre_publish(
        current_prompt="You are Gazer.",
        candidate_prompt="x",
        feedbacks=[{"label": "negative"}],
    )
    assert payload["passed"] is False
    assert payload["reason"] == "score_below_threshold"
    assert calls["set_gate"] == 1


@pytest.mark.asyncio
async def test_auto_optimize_disabled(tmp_dir, monkeypatch):
    evo = _AutoEvo(feedback_path=str(tmp_dir / "feedback.json"))
    monkeypatch.setattr(
        evolution_mod,
        "config",
        _ConfigStub(
            {
                "personality": {
                    "evolution": {
                        "auto_optimize": {
                            "enabled": False,
                        }
                    }
                }
            }
        ),
    )
    evo.collect_feedback("negative", "web", "bad")
    result = await evo.maybe_auto_optimize()
    assert result["attempted"] is False
    assert result["reason"] == "disabled"
    assert evo.optimize_calls == 0


@pytest.mark.asyncio
async def test_auto_optimize_threshold_not_met(tmp_dir, monkeypatch):
    evo = _AutoEvo(feedback_path=str(tmp_dir / "feedback.json"))
    monkeypatch.setattr(
        evolution_mod,
        "config",
        _ConfigStub(
            {
                "personality": {
                    "evolution": {
                        "auto_optimize": {
                            "enabled": True,
                            "min_feedback_total": 3,
                            "min_actionable_feedback": 2,
                            "cooldown_seconds": 0,
                        }
                    }
                }
            }
        ),
    )
    evo.collect_feedback("negative", "web", "one")
    result = await evo.maybe_auto_optimize()
    assert result["attempted"] is False
    assert result["reason"] == "insufficient_total_feedback"
    assert evo.optimize_calls == 0


@pytest.mark.asyncio
async def test_auto_optimize_respects_cooldown(tmp_dir, monkeypatch):
    evo = _AutoEvo(feedback_path=str(tmp_dir / "feedback.json"))
    monkeypatch.setattr(
        evolution_mod,
        "config",
        _ConfigStub(
            {
                "personality": {
                    "evolution": {
                        "auto_optimize": {
                            "enabled": True,
                            "min_feedback_total": 1,
                            "min_actionable_feedback": 0,
                            "cooldown_seconds": 3600,
                        }
                    }
                }
            }
        ),
    )
    evo.collect_feedback("positive", "web", "good")
    first = await evo.maybe_auto_optimize()
    second = await evo.maybe_auto_optimize()
    assert first["attempted"] is True
    assert first["updated"] is True
    assert second["attempted"] is False
    assert second["reason"] == "cooldown_active"
    assert evo.optimize_calls == 1


@pytest.mark.asyncio
async def test_auto_optimize_records_history(tmp_dir, monkeypatch):
    evo = _AutoEvo(
        feedback_path=str(tmp_dir / "feedback.json"),
    )
    evo.history_path = str(tmp_dir / "evolution_history.jsonl")
    monkeypatch.setattr(
        evolution_mod,
        "config",
        _ConfigStub(
            {
                "personality": {
                    "evolution": {
                        "auto_optimize": {
                            "enabled": False,
                        },
                        "history": {
                            "max_records": 20,
                        },
                    }
                }
            }
        ),
    )
    await evo.maybe_auto_optimize()
    history = evo.get_recent_history(limit=5)
    assert len(history) >= 1
    assert history[-1]["event"] == "auto_optimize"
    assert history[-1]["reason"] == "disabled"


def test_history_summary_and_clear(tmp_dir):
    evo = GazerEvolution(feedback_path=str(tmp_dir / "feedback.json"))
    evo.history_path = str(tmp_dir / "evolution_history.jsonl")
    evo._record_history_event({"event": "auto_optimize", "reason": "disabled", "updated": False})
    evo._record_history_event({"event": "optimize_persona", "reason": "updated", "updated": True})
    summary = evo.get_history_summary()
    assert summary["total"] == 2
    assert summary["updated"] == 1
    assert summary["by_event"]["auto_optimize"] == 1
    cleared = evo.clear_history()
    assert cleared == 2
    assert evo.get_recent_history(limit=10) == []
