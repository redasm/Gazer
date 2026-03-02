"""Data models for GazerFlow workflow engine."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FlowArg:
    """Declared workflow argument."""
    name: str
    type: str = "string"
    default: Any = None
    description: str = ""


@dataclass
class FlowApproval:
    """Approval gate definition within a step."""
    prompt: str
    preview: Optional[str] = None  # expression to show user


@dataclass
class FlowStep:
    """A single step in a workflow pipeline."""
    id: str
    tool: Optional[str] = None       # tool name to call (or "llm_task")
    args: Dict[str, Any] = field(default_factory=dict)
    condition: Optional[str] = None  # Python expression (evaluated in sandbox)
    approve: Optional[FlowApproval] = None
    depends_on: List[str] = field(default_factory=list)  # prerequisite step IDs
    retry_max: int = 0  # retry count on step error
    retry_backoff_ms: int = 0  # linear backoff between retries
    timeout_ms: Optional[int] = None  # per-step timeout
    each: Optional[str] = None       # expression yielding iterable for fan-out
    on_complete: Optional[Dict[str, str]] = None  # state updates on success


@dataclass
class FlowConfig:
    """Runtime constraints for a workflow."""
    timeout_ms: int = 60000
    max_output_bytes: int = 512000
    retry_budget: int = 8


@dataclass
class FlowDefinition:
    """Parsed workflow definition from a .flow.yaml file."""
    name: str
    description: str = ""
    args: Dict[str, FlowArg] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)  # default state values
    steps: List[FlowStep] = field(default_factory=list)
    config: FlowConfig = field(default_factory=FlowConfig)
    source_path: Optional[str] = None  # path to the .flow.yaml


@dataclass
class StepResult:
    """Result of executing a single step."""
    output: Any = None
    skipped: bool = False
    error: Optional[str] = None


@dataclass
class FlowContext:
    """Runtime context passed through the pipeline."""
    args: Dict[str, Any] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)
    steps: Dict[str, StepResult] = field(default_factory=dict)
    # For `each` iteration
    item: Any = None
    item_index: int = 0


@dataclass
class FlowResult:
    """Result returned by FlowEngine.run() or resume()."""
    status: str  # "completed" | "needs_approval" | "error"
    output: Optional[Dict[str, StepResult]] = None

    # Approval-specific fields
    pending_step: Optional[str] = None
    prompt: Optional[str] = None
    preview: Any = None
    resume_token: Optional[str] = None

    # Error
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for tool output."""
        d: Dict[str, Any] = {"status": self.status}
        if self.output:
            d["steps"] = {
                k: {"output": v.output, "skipped": v.skipped, "error": v.error}
                for k, v in self.output.items()
            }
        if self.pending_step:
            d["pending_step"] = self.pending_step
        if self.prompt:
            d["prompt"] = self.prompt
        if self.preview is not None:
            d["preview"] = self.preview
        if self.resume_token:
            d["resume_token"] = self.resume_token
        if self.error:
            d["error"] = self.error
        return d
