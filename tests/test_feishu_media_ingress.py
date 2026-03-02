from types import SimpleNamespace

import pytest

from channels.feishu import FeishuChannel, _parse_text_content


class _FakeResourceResponse:
    def __init__(self, *, ok: bool, data: bytes = b"", code: int = 0, msg: str = "ok") -> None:
        self._ok = ok
        self.code = code
        self.msg = msg
        self.file = SimpleNamespace(read=lambda: data)

    def success(self) -> bool:
        return self._ok


class _FakeMessageResourceAPI:
    def __init__(self, responses: list[_FakeResourceResponse]) -> None:
        self._responses = list(responses)
        self.calls = []

    def get(self, request):
        self.calls.append(request)
        if self._responses:
            return self._responses.pop(0)
        return _FakeResourceResponse(ok=False, code=500, msg="no response")


def _install_fake_request_builder(monkeypatch):
    import channels.feishu as feishu_mod

    class _Builder:
        def __init__(self) -> None:
            self.payload = {}

        def message_id(self, value):
            self.payload["message_id"] = value
            return self

        def file_key(self, value):
            self.payload["file_key"] = value
            return self

        def type(self, value):
            self.payload["type"] = value
            return self

        def build(self):
            return dict(self.payload)

    class _Req:
        @staticmethod
        def builder():
            return _Builder()

    monkeypatch.setattr(feishu_mod, "GetMessageResourceRequest", _Req)


def test_extract_resource_descriptor_for_audio():
    raw = '{"file_key":"fk_123"}'
    file_key, resource_type, ext, fallbacks = FeishuChannel._extract_resource_descriptor(raw, "audio")
    assert file_key == "fk_123"
    assert resource_type == "file"
    assert ext == ".mp3"
    assert "audio" in fallbacks


def test_download_message_resource_uses_fallback_type(monkeypatch, tmp_path):
    import channels.feishu as feishu_mod

    _install_fake_request_builder(monkeypatch)
    monkeypatch.setattr(
        feishu_mod,
        "save_media",
        lambda data, ext=".bin", prefix="feishu": str(tmp_path / f"{prefix}{ext}"),
    )

    api = _FakeMessageResourceAPI(
        responses=[
            _FakeResourceResponse(ok=False, code=400, msg="bad primary type"),
            _FakeResourceResponse(ok=True, data=b"abc"),
        ]
    )
    ch = object.__new__(FeishuChannel)
    ch.client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message_resource=api)))

    path = ch._download_message_resource(
        message_id="mid_1",
        raw_content='{"file_key":"fk_1"}',
        message_type="audio",
    )

    assert path is not None
    assert path.endswith("feishu_audio.mp3")
    assert len(api.calls) == 2
    assert api.calls[0]["type"] == "file"
    assert api.calls[1]["type"] == "audio"


def test_parse_media_content_includes_structured_details():
    parsed = _parse_text_content('{"file_name":"demo.wav","duration":3140,"file_key":"fk_1"}', "audio")
    assert "[audio]" in parsed
    assert "file_name=demo.wav" in parsed
    assert "duration=3140" in parsed


@pytest.mark.asyncio
async def test_augment_media_context_appends_analysis(monkeypatch, tmp_path):
    import channels.feishu as feishu_mod

    class _Cfg:
        @staticmethod
        def get(path, default=None):
            if path == "feishu.media_analysis":
                return {
                    "enabled": True,
                    "include_inbound_summary": True,
                }
            return default

    monkeypatch.setattr(feishu_mod, "config", _Cfg())

    ch = object.__new__(FeishuChannel)

    async def _fake_analyze(path, message_type, cfg):
        return f"analyzed:{message_type}"

    monkeypatch.setattr(ch, "_analyze_media_file", _fake_analyze)

    media_file = tmp_path / "a.png"
    media_file.write_bytes(b"123")
    text, metadata = await ch._augment_media_context(
        text="hello",
        media=[str(media_file)],
        metadata={"feishu_media": [{"path": str(media_file), "message_type": "image"}]},
    )

    assert "Feishu media analysis" in text
    assert "analyzed:image" in text
    assert "feishu_media_analysis" in metadata
