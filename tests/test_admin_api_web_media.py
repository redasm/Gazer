from types import SimpleNamespace
from tools.admin import api_facade as admin_api


def test_decode_web_media_entries_supports_data_url(monkeypatch):
    saved = []

    def _fake_save_media(data: bytes, ext: str = ".bin", prefix: str = "web") -> str:
        saved.append((len(data), ext, prefix))
        return f"data/media/{prefix}_1{ext}"

    monkeypatch.setattr("channels.media_utils.save_media", _fake_save_media)

    payload = {
        "media": [
            "data:text/plain;base64,SGVsbG8=",
        ]
    }
    media, metadata = admin_api._decode_web_media_entries(payload)
    assert len(media) == 1
    assert media[0].startswith("data/media/web_1")
    assert saved[0][0] == 5
    assert "web_media" in metadata


def test_decode_web_media_entries_supports_dict_b64(monkeypatch):
    monkeypatch.setattr(
        "channels.media_utils.save_media",
        lambda data, ext=".bin", prefix="web": f"data/media/{prefix}_2{ext}",
    )
    payload = {
        "media": [
            {
                "filename": "clip.mp3",
                "mime_type": "audio/mpeg",
                "data_b64": "QUJD",  # ABC
            }
        ]
    }
    media, metadata = admin_api._decode_web_media_entries(payload)
    assert media == ["data/media/web_2.mp3"]
    assert metadata["web_media"][0]["mime"] == "audio/mpeg"
