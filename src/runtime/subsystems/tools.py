"""Tool chain initializer — registers core tools, plugins, flows and security policy."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from runtime.paths import resolve_runtime_root
from runtime.protocols import ConfigProvider

logger = logging.getLogger("GazerBrain")


async def setup_tools(
    config: ConfigProvider,
    agent,
    capture_manager,
    device_registry,
    body,
    spatial,
    canvas_state,
    email_client,
    memory_manager,
    app_context,
    rust_sidecar_client,
    cron_run_callback,
) -> Dict[str, Any]:
    """Register all tools, plugins, workflows and skill loader.

    Returns a dict of created subsystem objects::

        {
            "hook_registry": HookRegistry,
            "cron_scheduler": Optional[CronScheduler],
            "flow_engine": FlowEngine,
            "plugin_loader": PluginLoader,
            "skill_loader": SkillLoader,
            "rust_sidecar_client": ...,
        }
    """
    from tools.coding import (
        ExecTool, ReadFileTool, WriteFileTool, EditFileTool,
        ListDirTool, FindFilesTool, GrepTool, ReadSkillTool,
    )
    from tools.system_tools import GetTimeTool, ImageAnalyzeTool
    from plugins.hooks import HookRegistry
    from plugins.loader import PluginLoader
    from skills.loader import SkillLoader
    from scheduler.cron import CronScheduler
    from runtime.rust_sidecar import (
        RustFileOperations, RustShellOperations, build_rust_sidecar_client_from_config,
    )
    from runtime.rust_gate import is_rust_gray_rollout_enabled
    from tools.sandbox import get_sandbox_operations
    from tools.remote_ops import get_ssh_operations

    workspace = resolve_runtime_root(config)
    coding_workspace = workspace

    # === Hook Registry ===
    hook_registry = HookRegistry()
    agent.loop.tools.set_hook_registry(hook_registry)

    # === Layer 1: Core tools ===
    sandbox_ops = get_sandbox_operations(config, sidecar_client=rust_sidecar_client)
    shell_ops = None
    file_ops = None
    runtime_backend = str(config.get("runtime.backend", "python") or "python").strip().lower()
    exec_backend = str(config.get("coding.exec_backend", "local") or "local").strip().lower()
    if exec_backend == "local" and runtime_backend == "rust":
        exec_backend = "rust"
    allow_local_fallback = bool(config.get("coding.allow_local_fallback", False))

    if exec_backend == "sandbox":
        if sandbox_ops:
            shell_ops = sandbox_ops[0]
            file_ops = sandbox_ops[1]
            logger.info("ExecTool backend set to sandbox (Docker).")
        else:
            msg = "coding.exec_backend=sandbox but sandbox is unavailable."
            if allow_local_fallback:
                logger.warning("%s Falling back to local.", msg)
            else:
                raise RuntimeError(f"{msg} Set coding.allow_local_fallback=true to allow fallback.")
    elif exec_backend == "ssh":
        ssh_enabled = bool(config.get("coding.ssh.enabled", False))
        ssh_host = str(config.get("coding.ssh.host", "") or "").strip()
        if ssh_enabled and ssh_host:
            try:
                shell_ops, file_ops = get_ssh_operations(config, sidecar_client=rust_sidecar_client)
                remote_workspace = str(config.get("coding.ssh.remote_workspace", ".") or ".").strip()
                coding_workspace = Path(remote_workspace)
                backend = str(config.get("runtime.backend", "python") or "python").strip().lower()
                logger.info("ExecTool backend set to SSH (%s) via %s.", ssh_host, backend)
            except Exception as exc:
                if allow_local_fallback:
                    logger.warning("Failed to initialize SSH exec backend: %s. Falling back to local.", exc)
                else:
                    raise RuntimeError("Failed to initialize SSH exec backend and fallback is disabled.") from exc
        else:
            msg = "coding.exec_backend=ssh but coding.ssh.enabled/host is not configured."
            if allow_local_fallback:
                logger.warning("%s Falling back to local.", msg)
            else:
                raise RuntimeError(f"{msg} Set coding.allow_local_fallback=true to allow fallback.")
    elif exec_backend == "rust":
        try:
            sidecar_client = rust_sidecar_client or build_rust_sidecar_client_from_config(config)
            probe = await sidecar_client.probe_minimal()
            rust_sidecar_client = sidecar_client
            fallback_shell_ops = None
            fallback_file_ops = None
            if allow_local_fallback or is_rust_gray_rollout_enabled(config):
                from tools.base import ShellOperations as _LocalShellOps, FileOperations as _LocalFileOps
                fallback_shell_ops = _LocalShellOps()
                fallback_file_ops = _LocalFileOps()
            shell_ops = RustShellOperations(sidecar_client, fallback_shell_ops=fallback_shell_ops)
            file_ops = RustFileOperations(sidecar_client, fallback_file_ops=fallback_file_ops)
            logger.info("ExecTool backend set to rust sidecar at %s.", probe.get("endpoint", sidecar_client.endpoint))
            logger.info("Rust sidecar probe ok: health=%s, version=%s", probe.get("health", {}), probe.get("version", {}))
        except Exception as exc:
            msg = f"coding.exec_backend=rust but rust sidecar is unavailable: {exc}"
            if allow_local_fallback:
                logger.warning("%s Falling back to local.", msg)
            else:
                raise RuntimeError(f"{msg} Set coding.allow_local_fallback=true to allow fallback.")
    elif exec_backend != "local":
        msg = f"Unknown coding.exec_backend={exec_backend}."
        if allow_local_fallback:
            logger.warning("%s Falling back to local.", msg)
        else:
            raise RuntimeError(f"{msg} Set coding.allow_local_fallback=true to allow fallback.")

    exec_tool = ExecTool(coding_workspace, shell_ops=shell_ops)
    read_skill_tool = ReadSkillTool()
    for tool in [
        exec_tool,
        ReadFileTool(coding_workspace, file_ops=file_ops, shell_ops=shell_ops),
        WriteFileTool(coding_workspace, file_ops=file_ops),
        EditFileTool(coding_workspace, file_ops=file_ops),
        ListDirTool(coding_workspace, shell_ops=shell_ops),
        FindFilesTool(coding_workspace, shell_ops=shell_ops),
        GrepTool(coding_workspace, shell_ops=shell_ops),
        GetTimeTool(),
        ImageAnalyzeTool(),
        read_skill_tool,
    ]:
        agent.register_tool(tool)

    # === Cron scheduler ===
    cron_scheduler: Optional[CronScheduler] = None
    if bool(config.get("scheduler.cron_enabled", True)):
        cron_scheduler = CronScheduler(run_callback=cron_run_callback)
        cron_scheduler.load()
        app_context.cron_scheduler = cron_scheduler

    # === Layer 2+3: Plugins ===
    services = {
        "capture_manager": capture_manager,
        "device_registry": device_registry,
        "body": body,
        "spatial": spatial,
        "canvas_state": canvas_state,
        "email_client": email_client,
        "cron_scheduler": cron_scheduler,
        "coding_shell_ops": shell_ops,
        "coding_file_ops": file_ops,
        "coding_workspace": coding_workspace,
    }
    plugin_loader = PluginLoader(workspace=workspace)
    plugin_loader.discover()

    skills_dirs = [
        workspace / "skills",
        Path.home() / ".gazer" / "skills",
        Path(__file__).resolve().parent.parent / "skills",
    ]
    skill_loader = SkillLoader(skills_dirs)
    skill_loader.discover()

    loaded_ids = plugin_loader.load_all(
        tool_registry=agent.loop.tools,
        hook_registry=hook_registry,
        workspace=workspace,
        bus=agent.bus,
        memory=memory_manager,
        skill_loader=skill_loader,
        services=services,
    )
    if loaded_ids:
        logger.info("Loaded plugins: %s", loaded_ids)
    if plugin_loader.failed_ids:
        logger.warning("Failed plugins: %s", plugin_loader.failed_ids)

    # === GazerFlow ===
    from flow.engine import FlowEngine
    from flow.tool import FlowRunTool

    flow_dirs = [workspace / "workflows", Path.home() / ".gazer" / "workflows"]
    flow_engine = FlowEngine(
        tool_registry=agent.loop.tools,
        llm_provider=agent.provider,
        flow_dirs=flow_dirs,
    )
    agent.register_tool(FlowRunTool(flow_engine))
    logger.info("GazerFlow loaded %d workflow(s).", len(flow_engine.list_flows()))

    # === Tool security policy ===
    denylist = config.get("security.tool_denylist", [])
    allowlist = config.get("security.tool_allowlist", [])
    if denylist:
        agent.loop.tools.set_denylist(denylist)
    if allowlist:
        agent.loop.tools.set_allowlist(allowlist)

    app_context.tool_registry = agent.loop.tools
    logger.info("Registered %d tools.", len(agent.loop.tools))

    # === Skill loader finalization ===
    agent.set_skill_loader(skill_loader)
    read_skill_tool.set_skill_loader(skill_loader)
    logger.info("Loaded %d skills into context.", len(skill_loader.skills))

    return {
        "hook_registry": hook_registry,
        "cron_scheduler": cron_scheduler,
        "flow_engine": flow_engine,
        "plugin_loader": plugin_loader,
        "skill_loader": skill_loader,
        "rust_sidecar_client": rust_sidecar_client,
    }
