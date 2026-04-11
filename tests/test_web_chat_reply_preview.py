from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHAT_PATH = PROJECT_ROOT / "web" / "src" / "pages" / "Chat.jsx"
CSS_PATH = PROJECT_ROOT / "web" / "src" / "index.css"


def test_chat_page_renders_reply_preview_card() -> None:
    source = CHAT_PATH.read_text(encoding="utf-8")

    assert "resolveReplyPreview" in source
    assert 'className="chat-reply-card"' in source
    assert 'className="chat-reply-kicker"' in source
    assert 'className="chat-reply-preview"' in source
    assert "Replying to" in source


def test_chat_reply_preview_styles_exist() -> None:
    source = CSS_PATH.read_text(encoding="utf-8")

    assert ".chat-reply-card" in source
    assert ".chat-reply-kicker" in source
    assert ".chat-reply-preview" in source
