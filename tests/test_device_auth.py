from tools.admin import workflows as admin_api


def test_validate_satellite_node_auth_success(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_api,
        "_get_satellite_node_config",
        lambda node_id: {"token": "secret-1"} if node_id == "sat-01" else {},
    )
    ok, message = admin_api._validate_satellite_node_auth("sat-01", "secret-1")
    assert ok is True
    assert message == ""


def test_validate_satellite_node_auth_rejects_missing_node() -> None:
    ok, message = admin_api._validate_satellite_node_auth("", "token")
    assert ok is False
    assert "Node ID" in message


def test_validate_satellite_node_auth_rejects_unconfigured_node(monkeypatch) -> None:
    monkeypatch.setattr(admin_api, "_get_satellite_node_config", lambda node_id: {})
    ok, message = admin_api._validate_satellite_node_auth("sat-02", "token")
    assert ok is False
    assert "not configured" in message


def test_validate_satellite_node_auth_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setattr(admin_api, "_get_satellite_node_config", lambda node_id: {"token": "real-token"})
    ok, message = admin_api._validate_satellite_node_auth("sat-03", "bad-token")
    assert ok is False
    assert "Invalid node token" in message
