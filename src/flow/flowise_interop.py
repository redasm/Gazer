"""Flowise <-> Gazer workflow graph conversion (minimal compatible subset)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_flowise_name(name: str) -> str:
    return _as_text(name).lower().replace(" ", "").replace("_", "").replace("-", "")


def _flowise_node_hints(node: Optional[Dict[str, Any]]) -> Set[str]:
    if not isinstance(node, dict):
        return set()
    data = _as_dict(node.get("data"))
    hints_raw: List[Any] = [
        data.get("name"),
        data.get("label"),
        data.get("category"),
        node.get("type"),
    ]
    hints_raw.extend(_as_list(data.get("baseClasses")))
    hints_raw.extend(_as_list(data.get("tags")))
    hints: Set[str] = set()
    for item in hints_raw:
        token = _normalize_flowise_name(_as_text(item))
        if token:
            hints.add(token)
    return hints


def _interop_error(
    *,
    level: str,
    code: str,
    message: str,
    node_id: str = "",
    node_name: str = "",
    edge_id: str = "",
    source: str = "",
    target: str = "",
) -> Dict[str, Any]:
    return {
        "level": _as_text(level) or "node",
        "code": _as_text(code) or "unknown_error",
        "reason": _as_text(code) or "unknown_error",  # backward-compatible alias
        "message": _as_text(message),
        "node_id": _as_text(node_id),
        "node_name": _as_text(node_name),
        "edge_id": _as_text(edge_id),
        "source": _as_text(source),
        "target": _as_text(target),
    }


def _infer_flowise_name(node: Dict[str, Any]) -> str:
    data = _as_dict(node.get("data"))
    candidates = [
        _as_text(data.get("name")),
        _as_text(data.get("label")),
        _as_text(node.get("type")),
    ]
    for item in candidates:
        if item:
            return item
    return "unknown"


def _node_kind_from_flowise(name: str, node: Optional[Dict[str, Any]] = None) -> str:
    key = _normalize_flowise_name(name)
    family = _flowise_family_from_name(name, node=node)
    hints = _flowise_node_hints(node)
    all_keys = {key} | hints
    if family in {"memory", "agent"}:
        return "prompt"
    if family in {"retriever", "toolchain", "loader", "splitter", "vectorstore"}:
        return "tool"
    if family == "parser":
        return "output"
    if family == "router":
        return "condition"
    if key in {
        "buffermemory",
        "bufferwindowmemory",
        "chatmemory",
        "conversationmemory",
        "summarizememory",
        "motorheadmemory",
    } or "memory" in key:
        return "prompt"
    if key in {
        "vectorstoreretriever",
        "selfqueryretriever",
        "multiqueretriever",
        "contextualcompressionretriever",
        "retrievalqachain",
    } or "retriever" in key or "retrieval" in key:
        return "tool"
    if key in {
        "agent",
        "conversationalagent",
        "openaiagent",
        "reactagent",
        "agentexecutor",
    } or key.endswith("agent"):
        return "prompt"
    if key in {"toolchain", "sequentialtoolchain"} or "toolchain" in key:
        return "tool"
    if key in {"start", "input", "chatinput", "question", "textinput", "questioninput", "promptinput"}:
        return "input"
    if key in {"output", "chatoutput", "answer", "finalanswer", "stringoutputparser", "jsonoutputparser"}:
        return "output"
    if "ifelse" in key or "condition" in key or "switch" in key or "branch" in key:
        return "condition"
    if "prompt" in key or key in {"llmchain", "chatprompttemplate"}:
        return "prompt"
    if "tool" in key or key in {"calculator", "serpapi", "websearch", "bravesearch", "tavilysearchresults"}:
        return "tool"
    if any("chatmemory" in token for token in all_keys):
        return "prompt"
    if any("retriever" in token or "retrieval" in token for token in all_keys):
        return "tool"
    if any("router" in token for token in all_keys):
        return "condition"
    if any("agent" in token for token in all_keys):
        return "prompt"
    return ""


def _flowise_family_from_name(name: str, node: Optional[Dict[str, Any]] = None) -> str:
    key = _normalize_flowise_name(name)
    hints = _flowise_node_hints(node)
    all_keys = {key} | hints
    joined = " ".join(sorted(all_keys))

    def _contains_any(tokens: List[str]) -> bool:
        return any(token in joined for token in tokens)

    if _contains_any(
        [
            "memory",
            "chatmemory",
            "buffermemory",
            "bufferwindowmemory",
            "conversationsummarymemory",
            "tokenbuffermemory",
            "summarymemory",
        ]
    ):
        return "memory"
    if _contains_any(
        [
            "retriever",
            "retrieval",
            "vectorstore",
            "historyaware",
            "parentdocument",
            "multiquery",
            "contextualcompression",
            "ensemble",
        ]
    ):
        return "retriever"
    if _contains_any(
        [
            "documentloader",
            "loader",
            "csvfile",
            "pdffile",
            "jsonfile",
            "textfile",
            "directory",
        ]
    ):
        return "loader"
    if _contains_any(["textsplitter", "splitter", "recursivecharactertextsplitter", "tokentextsplitter"]):
        return "splitter"
    if _contains_any(["vectorstore", "faiss", "chroma", "pinecone", "qdrant", "weaviate"]):
        return "vectorstore"
    if _contains_any(["outputparser", "stringoutputparser", "jsonoutputparser", "structuredoutputparser"]):
        return "parser"
    if _contains_any(["toolchain", "sequentialtoolchain", "multitoolchain", "apichain"]):
        return "toolchain"
    if _contains_any(
        [
            "agent",
            "agentexecutor",
            "openaifunctionsagent",
            "structuredchatagent",
            "planandexecute",
            "reactagent",
            "csvagent",
            "sqlagent",
        ]
    ):
        return "agent"
    if _contains_any(["router", "routerchain", "llmrouterchain", "multiroutechain", "conversationrouterchain"]):
        return "router"

    if key in {
        "buffermemory",
        "bufferwindowmemory",
        "chatmemory",
        "conversationmemory",
        "summarizememory",
        "motorheadmemory",
        "zepmemory",
        "redisbackedchatmemory",
        "upstashredisbackedchatmemory",
        "dynamodbchatmemory",
    } or "memory" in key:
        return "memory"
    if key in {
        "vectorstoreretriever",
        "selfqueryretriever",
        "multiqueretriever",
        "contextualcompressionretriever",
        "retrievalqachain",
        "multivectorretriever",
        "parentdocumentretriever",
        "ensembleRetriever",
    } or "retriever" in key or "retrieval" in key:
        return "retriever"
    if key in {
        "documentloader",
        "csvfile",
        "pdffile",
        "jsonfile",
        "textfile",
        "directory",
        "directoryloader",
    } or key.endswith("loader") or "loader" in key:
        return "loader"
    if key in {
        "textsplitter",
        "recursivecharactertextsplitter",
        "tokentextsplitter",
        "markdowntextsplitter",
    } or "splitter" in key:
        return "splitter"
    if key in {
        "faiss",
        "chroma",
        "pinecone",
        "qdrant",
        "weaviate",
        "supabasevectorstore",
        "inmemoryvectorstore",
    } or "vectorstore" in key:
        return "vectorstore"
    if key in {"stringoutputparser", "jsonoutputparser", "structuredoutputparser"} or "outputparser" in key:
        return "parser"
    if key in {"toolchain", "sequentialtoolchain", "multitoolchain"} or "toolchain" in key:
        return "toolchain"
    if key in {
        "agent",
        "conversationalagent",
        "openaiagent",
        "reactagent",
        "agentexecutor",
        "csvagent",
        "sqlagent",
    } or key.endswith("agent"):
        return "agent"
    if key in {
        "router",
        "routerchain",
        "llmrouterchain",
        "multiroutechain",
        "conversationrouterchain",
    } or "router" in key:
        return "router"
    return "generic"


def _tool_name_from_flowise(name: str) -> str:
    key = _normalize_flowise_name(name)
    if "retriever" in key or "retrieval" in key:
        return "web_search"
    if key in {
        "serpapi",
        "websearch",
        "bravesearch",
        "serper",
        "duckduckgosearch",
        "tavilysearchresults",
        "wikipediatool",
    }:
        return "web_search"
    if key in {"webfetch", "urlfetch", "httpget", "httprequest", "requestsget", "webbrowsertool"}:
        return "web_fetch"
    if key in {"calculator", "math"}:
        return "echo"
    if any(token in key for token in {"loader", "splitter", "vectorstore", "embedding", "parser"}):
        return "echo"
    if "tool" in key:
        return "echo"
    return "echo"


def _flowise_node_to_gazer(node: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    node_id = _as_text(node.get("id"))
    if not node_id:
        return None, _interop_error(level="node", code="missing_id", message="Node id is required")
    name = _infer_flowise_name(node)
    kind = _node_kind_from_flowise(name, node=node)
    if not kind:
        return None, _interop_error(
            level="node",
            code="unsupported_node_type",
            node_id=node_id,
            node_name=name,
            message=f"Unsupported Flowise node type '{name}'",
        )
    data = _as_dict(node.get("data"))
    inputs = _as_dict(data.get("inputs"))
    normalized_name = _normalize_flowise_name(name)
    flowise_family = _flowise_family_from_name(name, node=node)
    cfg: Dict[str, Any] = {}
    if kind == "input":
        cfg = {"default": _as_text(inputs.get("default", ""))}
    elif kind == "prompt":
        if flowise_family == "memory":
            prompt = (
                _as_text(inputs.get("memoryPrompt"))
                or _as_text(inputs.get("template"))
                or _as_text(inputs.get("prompt"))
                or "{{prev}}"
            )
        elif flowise_family == "agent":
            prompt = (
                _as_text(inputs.get("systemMessage"))
                or _as_text(inputs.get("template"))
                or _as_text(inputs.get("prompt"))
                or "You are a helpful assistant."
            )
        else:
            prompt = (
                _as_text(inputs.get("template"))
                or _as_text(inputs.get("prompt"))
                or _as_text(inputs.get("systemMessage"))
                or _as_text(inputs.get("memoryPrompt"))
                or "{{prev}}"
            )
        if flowise_family == "agent" and "{{prev}}" not in prompt:
            prompt = f"{prompt}\n\nUser Input: {{{{prev}}}}".strip()
        cfg = {"prompt": prompt}
    elif kind == "tool":
        if flowise_family == "retriever":
            query = _as_text(inputs.get("query")) or "{{prev}}"
            cfg = {"tool_name": "web_search", "args": {"q": query}}
        elif flowise_family == "toolchain":
            chain_text = _as_text(inputs.get("chainInput")) or "{{prev}}"
            tool_name = _as_text(inputs.get("toolName")) or _tool_name_from_flowise(name)
            cfg = {"tool_name": tool_name or "echo", "args": {"text": chain_text}}
        else:
            tool_name = _as_text(inputs.get("toolName")) or _tool_name_from_flowise(name)
            if not tool_name and ("retriever" in normalized_name or "retrieval" in normalized_name):
                tool_name = "web_search"
            if tool_name == "web_search":
                cfg = {"tool_name": tool_name, "args": {"q": "{{prev}}"}}
            elif tool_name == "web_fetch":
                cfg = {"tool_name": tool_name, "args": {"url": "{{prev}}"}}
            else:
                cfg = {"tool_name": tool_name, "args": {"text": "{{prev}}"}}
    elif kind == "condition":
        if flowise_family == "router":
            route_value = _as_text(inputs.get("route")) or _as_text(inputs.get("value")) or "default"
            route_key = _as_text(inputs.get("routeKey")) or "intent"
            cfg = {"operator": "contains", "value": route_value, "route_key": route_key}
        else:
            operator = _as_text(inputs.get("operator")) or "contains"
            value = _as_text(inputs.get("value")) or "yes"
            cfg = {"operator": operator, "value": value}
    elif kind == "output":
        cfg = {"text": _as_text(inputs.get("text")) or "{{prev}}"}
    cfg["_flowise"] = {"name": name, "family": flowise_family}

    pos = _as_dict(node.get("position"))
    out = {
        "id": node_id,
        "type": kind,
        "label": _as_text(data.get("label")) or name,
        "config": cfg,
        "position": {
            "x": int(pos.get("x", 40) or 40),
            "y": int(pos.get("y", 40) or 40),
        },
    }
    return out, None


def flowise_to_gazer(payload: Dict[str, Any]) -> Dict[str, Any]:
    source = _as_dict(payload.get("flowise")) if "flowise" in payload else _as_dict(payload)
    nodes_raw = _as_list(source.get("nodes"))
    edges_raw = _as_list(source.get("edges"))
    flow_name = _as_text(payload.get("name")) or _as_text(source.get("name")) or "flowise_imported"
    flow_desc = _as_text(payload.get("description")) or _as_text(source.get("description"))

    nodes: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    node_ids = set()
    for item in nodes_raw:
        node = _as_dict(item)
        converted, err = _flowise_node_to_gazer(node)
        if err:
            errors.append(err)
            continue
        if not converted:
            continue
        node_id = _as_text(converted.get("id"))
        if node_id in node_ids:
            errors.append(
                _interop_error(
                    level="node",
                    code="duplicate_node_id",
                    node_id=node_id,
                    message=f"Duplicate node id '{node_id}'",
                )
            )
            continue
        node_ids.add(node_id)
        nodes.append(converted)

    edges: List[Dict[str, Any]] = []
    edge_errors: List[Dict[str, Any]] = []
    for idx, item in enumerate(edges_raw):
        edge = _as_dict(item)
        source_id = _as_text(edge.get("source"))
        target_id = _as_text(edge.get("target"))
        if source_id not in node_ids or target_id not in node_ids:
            edge_errors.append(
                _interop_error(
                    level="edge",
                    code="edge_references_missing_node",
                    edge_id=_as_text(edge.get("id")) or f"edge_{idx}",
                    source=source_id,
                    target=target_id,
                    message=f"Edge references missing node: source={source_id}, target={target_id}",
                )
            )
            continue
        label = _as_text(edge.get("label")).lower()
        when = ""
        if label in {"true", "false", "default"}:
            when = label
        edges.append(
            {
                "id": _as_text(edge.get("id")) or f"edge_{idx}",
                "source": source_id,
                "target": target_id,
                "when": when,
            }
        )

    workflow = {
        "name": flow_name,
        "description": flow_desc,
        "nodes": nodes,
        "edges": edges,
    }
    all_errors = [*errors, *edge_errors]
    return {
        "workflow": workflow,
        "errors": all_errors,
        "error_count": len(all_errors),
    }


def flowise_migration_suggestion(node_name: str, node_payload: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Return migration suggestion for unsupported Flowise node."""
    key = _normalize_flowise_name(node_name)
    family = _flowise_family_from_name(node_name, node=node_payload)
    if family == "retriever" or "retriever" in key or "retrieval" in key:
        return {
            "replacement": "tool:web_search",
            "risk_rating": "medium",
            "migration_tier": "auto_replace",
            "note": "Retriever behavior maps to web_search; ranking/vector semantics may need manual tuning.",
        }
    if family in {"loader", "splitter", "vectorstore"}:
        return {
            "replacement": "tool:explicit_chain_steps",
            "risk_rating": "low",
            "migration_tier": "auto_replace",
            "note": "Loader/Splitter/VectorStore nodes should be flattened into explicit tool-chain steps.",
        }
    if family == "memory" or "memory" in key:
        return {
            "replacement": "prompt:memory_context",
            "risk_rating": "medium",
            "migration_tier": "manual_review",
            "note": "Memory node folds into prompt context; verify memory window and summarization policy.",
        }
    if family == "agent" or "agent" in key:
        return {
            "replacement": "prompt:agent_instruction",
            "risk_rating": "medium",
            "migration_tier": "manual_review",
            "note": "Agent node maps to prompt orchestration; review tool policy and execution loop manually.",
        }
    if family == "toolchain" or "toolchain" in key or "apichain" in key:
        return {
            "replacement": "tool:explicit_chain_steps",
            "risk_rating": "low",
            "migration_tier": "auto_replace",
            "note": "ToolChain behavior should be split into explicit tool nodes and edges.",
        }
    if family == "router" or "router" in key or "ifelse" in key:
        return {
            "replacement": "condition:ifElse",
            "risk_rating": "high",
            "migration_tier": "manual_review",
            "note": "Router logic is approximated by condition edges; advanced routing policy requires custom mapping.",
        }
    if any(token in key for token in {"pythonrepl", "shell", "terminal", "code"}):
        return {
            "replacement": "manual_mapping_required",
            "risk_rating": "high",
            "migration_tier": "manual_review",
            "note": "Code-execution style nodes require explicit security review before mapping.",
        }
    return {
        "replacement": "manual_mapping_required",
        "risk_rating": "high",
        "migration_tier": "manual_review",
        "note": "No safe automatic mapping found; requires manual workflow redesign.",
    }


def gazer_to_flowise(graph: Dict[str, Any]) -> Dict[str, Any]:
    nodes_raw = _as_list(graph.get("nodes"))
    edges_raw = _as_list(graph.get("edges"))
    flow_name = _as_text(graph.get("name")) or "gazer_export"
    flow_desc = _as_text(graph.get("description"))

    def _flowise_name(node_type: str, cfg: Dict[str, Any]) -> str:
        meta = _as_dict(cfg.get("_flowise"))
        explicit = _as_text(meta.get("name"))
        if explicit:
            return explicit
        family = _as_text(meta.get("family")).lower()
        if node_type == "prompt" and family == "memory":
            return "bufferWindowMemory"
        if node_type == "prompt" and family == "agent":
            return "conversationalAgent"
        if node_type == "tool" and family == "retriever":
            return "vectorStoreRetriever"
        if node_type == "tool" and family == "toolchain":
            return "toolChain"
        if node_type == "condition" and family == "router":
            return "llmRouterChain"
        mapping = {
            "input": "chatInput",
            "prompt": "chatPromptTemplate",
            "tool": "tool",
            "condition": "ifElse",
            "output": "chatOutput",
        }
        return mapping.get(node_type, "customNode")

    nodes: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []
    for item in nodes_raw:
        node = _as_dict(item)
        node_id = _as_text(node.get("id"))
        node_type = _as_text(node.get("type")).lower()
        if node_type not in {"input", "prompt", "tool", "condition", "output"}:
            unsupported.append(
                {"node_id": node_id, "reason": "unsupported_node_type", "message": f"Unsupported node type '{node_type}'"}
            )
            continue
        cfg = _as_dict(node.get("config"))
        meta = _as_dict(cfg.get("_flowise"))
        family = _as_text(meta.get("family")).lower()
        inputs: Dict[str, Any] = {}
        if node_type == "input":
            inputs["default"] = _as_text(cfg.get("default"))
        elif node_type == "prompt":
            prompt_text = _as_text(cfg.get("prompt")) or "{{prev}}"
            if family == "memory":
                inputs["memoryPrompt"] = prompt_text
            elif family == "agent":
                inputs["systemMessage"] = prompt_text
                inputs["template"] = prompt_text
            else:
                inputs["template"] = prompt_text
        elif node_type == "tool":
            inputs["toolName"] = _as_text(cfg.get("tool_name")) or "echo"
            inputs["args"] = _as_dict(cfg.get("args"))
            if family == "retriever":
                inputs["query"] = _as_text(_as_dict(cfg.get("args")).get("q")) or "{{prev}}"
            if family == "toolchain":
                inputs["chainInput"] = _as_text(_as_dict(cfg.get("args")).get("text")) or "{{prev}}"
        elif node_type == "condition":
            inputs["operator"] = _as_text(cfg.get("operator")) or "contains"
            inputs["value"] = _as_text(cfg.get("value")) or "yes"
            if family == "router":
                inputs["route"] = _as_text(cfg.get("value")) or "default"
                inputs["routeKey"] = _as_text(cfg.get("route_key")) or "intent"
        elif node_type == "output":
            inputs["text"] = _as_text(cfg.get("text")) or "{{prev}}"

        pos = _as_dict(node.get("position"))
        nodes.append(
            {
                "id": node_id,
                "type": "customNode",
                "position": {"x": int(pos.get("x", 40) or 40), "y": int(pos.get("y", 40) or 40)},
                "data": {
                    "id": node_id,
                    "label": _as_text(node.get("label")) or node_id,
                    "name": _flowise_name(node_type, cfg),
                    "inputs": inputs,
                },
            }
        )

    edges: List[Dict[str, Any]] = []
    for idx, item in enumerate(edges_raw):
        edge = _as_dict(item)
        label = _as_text(edge.get("when")).lower()
        if label not in {"true", "false", "default"}:
            label = ""
        edges.append(
            {
                "id": _as_text(edge.get("id")) or f"edge_{idx}",
                "source": _as_text(edge.get("source")),
                "target": _as_text(edge.get("target")),
                "label": label,
            }
        )

    return {
        "name": flow_name,
        "description": flow_desc,
        "nodes": nodes,
        "edges": edges,
        "unsupported_nodes": unsupported,
        "unsupported_count": len(unsupported),
    }
