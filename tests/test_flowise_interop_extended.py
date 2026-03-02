from flow.flowise_interop import flowise_to_gazer, gazer_to_flowise, flowise_migration_suggestion


def test_flowise_to_gazer_extended_node_mapping():
    payload = {
        "flowise": {
            "nodes": [
                {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                {"id": "mem1", "type": "customNode", "data": {"name": "bufferWindowMemory", "inputs": {"memoryPrompt": "Memory=>{{prev}}"}}},
                {"id": "ret1", "type": "customNode", "data": {"name": "vectorStoreRetriever"}},
                {"id": "agent1", "type": "customNode", "data": {"name": "conversationalAgent", "inputs": {"systemMessage": "Assistant mode"}}},
                {"id": "chain1", "type": "customNode", "data": {"name": "toolChain", "inputs": {"toolName": "echo"}}},
                {"id": "out1", "type": "customNode", "data": {"name": "chatOutput", "inputs": {"text": "{{prev}}"}}},
            ],
            "edges": [
                {"source": "in1", "target": "mem1"},
                {"source": "mem1", "target": "ret1"},
                {"source": "ret1", "target": "agent1"},
                {"source": "agent1", "target": "chain1"},
                {"source": "chain1", "target": "out1"},
            ],
        }
    }
    converted = flowise_to_gazer(payload)
    assert converted["error_count"] == 0
    nodes = {item["id"]: item for item in converted["workflow"]["nodes"]}
    assert nodes["mem1"]["type"] == "prompt"
    assert nodes["mem1"]["config"]["_flowise"]["family"] == "memory"
    assert nodes["ret1"]["type"] == "tool"
    assert nodes["ret1"]["config"]["tool_name"] == "web_search"
    assert nodes["ret1"]["config"]["_flowise"]["family"] == "retriever"
    assert nodes["agent1"]["type"] == "prompt"
    assert nodes["agent1"]["config"]["_flowise"]["family"] == "agent"
    assert nodes["chain1"]["type"] == "tool"
    assert nodes["chain1"]["config"]["_flowise"]["family"] == "toolchain"


def test_flowise_to_gazer_unknown_node_still_reports_error():
    payload = {
        "flowise": {
            "nodes": [
                {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                {"id": "bad1", "type": "customNode", "data": {"name": "unsupportedMysteryNode"}},
                {"id": "out1", "type": "customNode", "data": {"name": "chatOutput"}},
            ],
            "edges": [
                {"source": "in1", "target": "bad1"},
                {"source": "bad1", "target": "out1"},
            ],
        }
    }
    converted = flowise_to_gazer(payload)
    assert converted["error_count"] >= 1
    assert any(item.get("node_id") == "bad1" for item in converted.get("errors", []))
    assert any(item.get("level") == "node" and item.get("code") == "unsupported_node_type" for item in converted.get("errors", []))


def test_flowise_to_gazer_router_node_maps_to_condition():
    payload = {
        "flowise": {
            "nodes": [
                {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                {
                    "id": "router1",
                    "type": "customNode",
                    "data": {"name": "llmRouterChain", "inputs": {"route": "image", "routeKey": "intent"}},
                },
                {"id": "out1", "type": "customNode", "data": {"name": "chatOutput"}},
            ],
            "edges": [
                {"source": "in1", "target": "router1"},
                {"source": "router1", "target": "out1", "label": "true"},
            ],
        }
    }
    converted = flowise_to_gazer(payload)
    assert converted["error_count"] == 0
    nodes = {item["id"]: item for item in converted["workflow"]["nodes"]}
    assert nodes["router1"]["type"] == "condition"
    assert nodes["router1"]["config"]["operator"] == "contains"
    assert nodes["router1"]["config"]["value"] == "image"
    assert nodes["router1"]["config"]["route_key"] == "intent"
    assert nodes["router1"]["config"]["_flowise"]["family"] == "router"


def test_gazer_to_flowise_preserves_extended_family_name_hints():
    graph = {
        "name": "interop_family",
        "nodes": [
            {"id": "in1", "type": "input", "config": {"default": ""}},
            {
                "id": "m1",
                "type": "prompt",
                "config": {"prompt": "Memory=>{{prev}}", "_flowise": {"family": "memory"}},
            },
            {
                "id": "r1",
                "type": "tool",
                "config": {"tool_name": "web_search", "args": {"q": "{{prev}}"}, "_flowise": {"family": "retriever"}},
            },
            {
                "id": "router1",
                "type": "condition",
                "config": {"operator": "contains", "value": "image", "route_key": "intent", "_flowise": {"family": "router"}},
            },
            {"id": "out1", "type": "output", "config": {"text": "{{prev}}"}},
        ],
        "edges": [
            {"source": "in1", "target": "m1"},
            {"source": "m1", "target": "r1"},
            {"source": "r1", "target": "router1"},
            {"source": "router1", "target": "out1", "when": "true"},
        ],
    }
    exported = gazer_to_flowise(graph)
    names = {item["id"]: item.get("data", {}).get("name", "") for item in exported["nodes"]}
    assert names["m1"] == "bufferWindowMemory"
    assert names["r1"] == "vectorStoreRetriever"
    assert names["router1"] == "llmRouterChain"
    router_inputs = next(item["data"]["inputs"] for item in exported["nodes"] if item["id"] == "router1")
    assert router_inputs["route"] == "image"
    assert router_inputs["routeKey"] == "intent"


def test_flowise_to_gazer_supports_baseclass_hint_mapping():
    payload = {
        "flowise": {
            "nodes": [
                {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                {
                    "id": "xmem",
                    "type": "customNode",
                    "data": {"name": "mysteryNode", "baseClasses": ["BaseChatMemory"], "inputs": {"memoryPrompt": "m={{prev}}"}},
                },
                {
                    "id": "xret",
                    "type": "customNode",
                    "data": {"name": "mysteryRetriever", "baseClasses": ["BaseRetriever"]},
                },
                {"id": "out1", "type": "customNode", "data": {"name": "chatOutput"}},
            ],
            "edges": [
                {"source": "in1", "target": "xmem"},
                {"source": "xmem", "target": "xret"},
                {"source": "xret", "target": "out1"},
            ],
        }
    }
    converted = flowise_to_gazer(payload)
    assert converted["error_count"] == 0
    nodes = {item["id"]: item for item in converted["workflow"]["nodes"]}
    assert nodes["xmem"]["type"] == "prompt"
    assert nodes["xmem"]["config"]["_flowise"]["family"] == "memory"
    assert nodes["xret"]["type"] == "tool"
    assert nodes["xret"]["config"]["tool_name"] == "web_search"
    assert nodes["xret"]["config"]["_flowise"]["family"] == "retriever"


def test_flowise_to_gazer_supports_loader_splitter_low_risk_mapping():
    payload = {
        "flowise": {
            "nodes": [
                {"id": "in1", "type": "customNode", "data": {"name": "chatInput"}},
                {"id": "loader1", "type": "customNode", "data": {"name": "documentLoader"}},
                {"id": "split1", "type": "customNode", "data": {"name": "recursiveCharacterTextSplitter"}},
                {"id": "out1", "type": "customNode", "data": {"name": "chatOutput"}},
            ],
            "edges": [
                {"source": "in1", "target": "loader1"},
                {"source": "loader1", "target": "split1"},
                {"source": "split1", "target": "out1"},
            ],
        }
    }
    converted = flowise_to_gazer(payload)
    assert converted["error_count"] == 0
    nodes = {item["id"]: item for item in converted["workflow"]["nodes"]}
    assert nodes["loader1"]["type"] == "tool"
    assert nodes["loader1"]["config"]["_flowise"]["family"] == "loader"
    assert nodes["split1"]["type"] == "tool"
    assert nodes["split1"]["config"]["_flowise"]["family"] == "splitter"


def test_flowise_migration_suggestion_rule_library():
    retriever = flowise_migration_suggestion("historyAwareRetriever")
    assert retriever["replacement"] == "tool:web_search"
    assert retriever["risk_rating"] == "medium"
    assert retriever["migration_tier"] == "auto_replace"

    chain = flowise_migration_suggestion("apiChain")
    assert chain["replacement"] == "tool:explicit_chain_steps"
    assert chain["risk_rating"] == "low"
    assert chain["migration_tier"] == "auto_replace"

    code = flowise_migration_suggestion("pythonREPLTool")
    assert code["replacement"] == "manual_mapping_required"
    assert code["risk_rating"] == "high"
    assert code["migration_tier"] == "manual_review"
