"""Tests for memory.emotions -- EmotionAnalyzer + EmotionTracker."""

import os
import json
import pytest
from memory.emotions import EmotionAnalyzer, EmotionTracker, EmotionSnapshot


class TestEmotionAnalyzer:
    def setup_method(self):
        self.analyzer = EmotionAnalyzer()

    def test_happy_keywords(self):
        emotion, sentiment = self.analyzer.analyze("今天太棒了，真开心！")
        assert emotion == "happy"
        assert sentiment > 0

    def test_sad_keywords(self):
        emotion, sentiment = self.analyzer.analyze("好难过，真失望")
        assert emotion == "sad"
        assert sentiment < 0

    def test_anxious_keywords(self):
        emotion, sentiment = self.analyzer.analyze("好焦虑，压力很大")
        assert emotion == "anxious"
        assert sentiment < 0

    def test_angry_keywords(self):
        emotion, sentiment = self.analyzer.analyze("气死了，真讨厌")
        assert emotion == "angry"
        assert sentiment < 0

    def test_neutral_no_keywords(self):
        emotion, sentiment = self.analyzer.analyze("The weather is sunny today")
        assert emotion == "neutral"
        assert sentiment == 0.0

    def test_extract_topics_work(self):
        topics = self.analyzer.extract_topics("今天工作很忙，项目要赶")
        assert "工作" in topics

    def test_extract_topics_health(self):
        topics = self.analyzer.extract_topics("头疼，去医院看看")
        assert "健康" in topics

    def test_extract_topics_empty(self):
        topics = self.analyzer.extract_topics("hello world")
        assert topics == []


class TestEmotionTracker:
    @pytest.fixture
    def tracker(self, tmp_dir):
        return EmotionTracker(storage_dir=str(tmp_dir / "emotions"))

    def test_analyze_user_message(self, tracker):
        emotion, sentiment, topics = tracker.analyze_message("太开心了！", sender="user")
        assert emotion == "happy"
        assert sentiment > 0

    def test_skip_non_user_message(self, tracker):
        emotion, sentiment, topics = tracker.analyze_message("I am sad", sender="assistant")
        assert emotion == "neutral"
        assert sentiment == 0.0

    def test_today_summary(self, tracker):
        tracker.analyze_message("真开心", sender="user")
        summary = tracker.get_today_summary()
        assert "开心" in summary or "消息" in summary

    def test_snapshot_persistence(self, tracker, tmp_dir):
        tracker.analyze_message("太棒了！", sender="user")
        tracker.analyze_message("好高兴", sender="user")
        assert tracker._today_data.message_count == 2

        # Check file was written
        from datetime import date
        snapshot_file = os.path.join(str(tmp_dir / "emotions"), f"{date.today().isoformat()}.json")
        assert os.path.exists(snapshot_file)
        with open(snapshot_file, "r") as f:
            data = json.load(f)
        assert data["message_count"] == 2

    def test_add_highlight(self, tracker):
        tracker.add_highlight("Got a new job offer!")
        assert len(tracker._today_data.highlights) == 1

    def test_get_recent_mood_empty(self, tracker):
        mood = tracker.get_recent_mood(days=7)
        # Should return "not enough records" message since no data
        assert isinstance(mood, str)


class TestEmotionSnapshot:
    def test_defaults(self):
        snap = EmotionSnapshot(date="2026-02-05")
        assert snap.overall_mood == "neutral"
        assert snap.avg_sentiment == 0.0
        assert snap.message_count == 0
