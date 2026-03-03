from tools.admin import api_facade as admin_api


class _FakeTrajectoryStore:
    def list_recent(self, limit=50, session_key=None):
        return [{"run_id": "r1"}]

    def get_trajectory(self, run_id):
        return {
            "events": [
                {
                    "action": "inbound_metadata",
                    "payload": {
                        "metadata": {
                            "feishu_message_type": "image",
                            "feishu_media": [{"path": "data/media/feishu_img_1.png", "message_type": "image"}],
                        }
                    },
                },
                {
                    "action": "inbound_metadata",
                    "payload": {
                        "metadata": {
                            "web_media": [
                                {"source": "url", "url": "https://example.com/a.png"},
                                {"source": "b64", "path": "data/media/web_1.mp3", "mime": "audio/mpeg"},
                            ]
                        }
                    },
                },
                {
                    "action": "inbound_metadata",
                    "payload": {"metadata": {"telegram_message_type": "voice"}},
                },
            ]
        }


def test_build_inbound_media_profile(monkeypatch):
    monkeypatch.setattr(admin_api, "TRAJECTORY_STORE", _FakeTrajectoryStore())
    profile = admin_api._build_inbound_media_profile(limit=20)
    assert profile["events"] == 3
    assert profile["media_entries"] == 4
    assert profile["successful_entries"] == 4
    assert profile["failed_entries"] == 0
    assert profile["by_source"]["feishu"] == 1
    assert profile["by_source"]["web"] == 1
    assert profile["by_source"]["telegram"] == 1
