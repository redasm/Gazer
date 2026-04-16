"""Skill / tool description evolution engine.

Analyses tool failure patterns from trajectory data and proposes improved
tool descriptions. Proposals go through a safety gate (size + semantic
preservation) before being stored. A human approval step is required before
any proposal can be applied to the live config.

Architecture:
  - ``ToolFailureProfile``  — per-tool failure statistics extracted from trajectories
  - ``SkillEvolutionProposal`` — a concrete description improvement with safety check
  - ``SkillEvolver``         — analysis + generation + persistence + apply logic

Persistence: ``~/.gazer/eval/skill_evolution_proposals.jsonl``
Config applied to: ``skill_overrides`` section of the config (never source code).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_\u4e00-\u9fff]+")


def _tokenize(text: str) -> set:
    return {t.lower() for t in _TOKEN_RE.findall(str(text or ""))}


def _jaccard(a: str, b: str) -> float:
    sa = _tokenize(a)
    sb = _tokenize(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _proposal_id() -> str:
    return f"prop_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ToolFailureProfile:
    tool_name: str
    failure_count: int
    error_codes: Dict[str, int]      # {error_code: count}
    sample_bad_inputs: List[str]     # up to 5 user inputs that triggered failures
    current_description: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillEvolutionProposal:
    proposal_id: str
    tool_name: str
    current_description: str
    proposed_description: str
    rationale: str
    safety_check: Dict[str, Any]     # size_ok, semantic_score, key_terms_retained, ok
    status: str                      # pending | approved | rejected | applied
    created_at: float
    updated_at: float
    actor: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillEvolutionProposal":
        return cls(
            proposal_id=str(data.get("proposal_id", "")),
            tool_name=str(data.get("tool_name", "")),
            current_description=str(data.get("current_description", "")),
            proposed_description=str(data.get("proposed_description", "")),
            rationale=str(data.get("rationale", "")),
            safety_check=data.get("safety_check") if isinstance(data.get("safety_check"), dict) else {},
            status=str(data.get("status", "pending")),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
            actor=str(data.get("actor", "")),
            note=str(data.get("note", "")),
        )


# ---------------------------------------------------------------------------
# SkillEvolver
# ---------------------------------------------------------------------------

class SkillEvolver:
    """Analyses tool failure patterns and proposes description improvements.

    Parameters
    ----------
    base_dir:
        Directory for persisting proposals. Defaults to ``~/.gazer/eval``.
    max_description_chars:
        Hard limit on proposed description length (matches hermes's 500-char rule).
    min_semantic_preservation:
        Minimum Jaccard similarity between original and proposed description
        tokens. Proposals below this threshold are rejected by the safety check.
    """

    MAX_DESCRIPTION_CHARS = 500
    MIN_SEMANTIC_PRESERVATION = 0.75

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        *,
        max_description_chars: int = 500,
        min_semantic_preservation: float = 0.75,
    ) -> None:
        self._base = base_dir or (Path.home() / ".gazer" / "eval")
        self._proposals_path = self._base / "skill_evolution_proposals.jsonl"
        self.max_description_chars = max(50, int(max_description_chars))
        self.min_semantic_preservation = max(0.0, min(1.0, float(min_semantic_preservation)))

    # ------------------------------------------------------------------
    # Failure analysis
    # ------------------------------------------------------------------

    def analyze_tool_failures(
        self,
        trajectory_samples: List[Dict[str, Any]],
        *,
        top_n: int = 5,
        skill_descriptions: Optional[Dict[str, str]] = None,
    ) -> List[ToolFailureProfile]:
        """Extract per-tool failure profiles from trajectory samples.

        ``trajectory_samples`` may be either raw trajectory dicts (with
        ``events`` list) or bridge-export samples (with ``tool_result.events``).
        """
        descriptions = skill_descriptions or {}
        tool_stats: Dict[str, Dict[str, Any]] = {}

        for item in trajectory_samples:
            if not isinstance(item, dict):
                continue
            user_input = (
                str(item.get("state", {}).get("user_content", ""))
                or str(item.get("user_content", ""))
            )
            events = self._extract_events(item)

            for ev in events:
                if not isinstance(ev, dict):
                    continue
                tool = str(ev.get("tool", "")).strip().lower()
                status = str(ev.get("status", "")).strip().lower()
                error_code = str(ev.get("error_code", "")).strip().lower()

                if not tool or status in {"ok", "success"}:
                    continue
                if tool not in tool_stats:
                    tool_stats[tool] = {
                        "failure_count": 0,
                        "error_codes": {},
                        "bad_inputs": [],
                    }
                tool_stats[tool]["failure_count"] += 1
                if error_code:
                    tool_stats[tool]["error_codes"][error_code] = (
                        tool_stats[tool]["error_codes"].get(error_code, 0) + 1
                    )
                if user_input and len(tool_stats[tool]["bad_inputs"]) < 5:
                    if user_input not in tool_stats[tool]["bad_inputs"]:
                        tool_stats[tool]["bad_inputs"].append(user_input)

        sorted_tools = sorted(
            tool_stats.items(),
            key=lambda kv: kv[1]["failure_count"],
            reverse=True,
        )[:top_n]

        profiles: List[ToolFailureProfile] = []
        for tool_name, stats in sorted_tools:
            profiles.append(
                ToolFailureProfile(
                    tool_name=tool_name,
                    failure_count=int(stats["failure_count"]),
                    error_codes=dict(stats["error_codes"]),
                    sample_bad_inputs=list(stats["bad_inputs"]),
                    current_description=str(descriptions.get(tool_name, "")),
                )
            )
        return profiles

    # ------------------------------------------------------------------
    # Safety check
    # ------------------------------------------------------------------

    def safety_check(
        self,
        original: str,
        proposed: str,
    ) -> Dict[str, Any]:
        """Validate a proposed description against size and semantic constraints."""
        size_ok = len(proposed) <= self.max_description_chars
        semantic_score = round(_jaccard(original, proposed), 4)
        key_terms_retained = semantic_score >= self.min_semantic_preservation
        ok = size_ok and key_terms_retained
        return {
            "ok": ok,
            "size_ok": size_ok,
            "proposed_length": len(proposed),
            "max_length": self.max_description_chars,
            "semantic_score": semantic_score,
            "min_semantic_preservation": self.min_semantic_preservation,
            "key_terms_retained": key_terms_retained,
        }

    # ------------------------------------------------------------------
    # Proposal generation
    # ------------------------------------------------------------------

    def generate_proposals(
        self,
        profiles: List[ToolFailureProfile],
        *,
        llm_caller: Optional[Callable] = None,
        max_proposals: int = 10,
    ) -> List[SkillEvolutionProposal]:
        """Generate description improvement proposals for the given profiles.

        With ``llm_caller`` provided, calls the LLM for each profile.
        Without LLM, uses a template-based heuristic (useful for testing).
        """
        proposals: List[SkillEvolutionProposal] = []
        for profile in profiles[:max_proposals]:
            if llm_caller is not None:
                proposed, rationale = self._llm_propose(profile, llm_caller)
            else:
                proposed, rationale = self._heuristic_propose(profile)

            check = self.safety_check(profile.current_description, proposed)
            if not check["ok"]:
                continue

            now = time.time()
            proposals.append(
                SkillEvolutionProposal(
                    proposal_id=_proposal_id(),
                    tool_name=profile.tool_name,
                    current_description=profile.current_description,
                    proposed_description=proposed,
                    rationale=rationale,
                    safety_check=check,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
        return proposals

    @staticmethod
    def _llm_propose(
        profile: ToolFailureProfile,
        llm_caller: Callable,
    ) -> tuple:
        prompt = (
            f"You are improving a tool description to reduce user-facing failures.\n\n"
            f"Tool: {profile.tool_name}\n"
            f"Current description: {profile.current_description or '(none)'}\n"
            f"Failure count: {profile.failure_count}\n"
            f"Top error codes: {list(profile.error_codes.keys())[:3]}\n"
            f"Sample inputs that caused failures:\n"
            + "\n".join(f"  - {inp}" for inp in profile.sample_bad_inputs[:3])
            + "\n\n"
            f"Write an improved description that clarifies correct usage and prevents "
            f"these errors. Max 500 characters. Return JSON: "
            f'{{ "proposed": "...", "rationale": "..." }}'
        )
        try:
            raw = llm_caller(prompt)
            parsed = json.loads(raw)
            proposed = str(parsed.get("proposed", "")).strip()
            rationale = str(parsed.get("rationale", "")).strip()
            if proposed:
                return proposed, rationale
        except Exception:
            pass
        return SkillEvolver._heuristic_propose(profile)

    @staticmethod
    def _heuristic_propose(profile: ToolFailureProfile) -> tuple:
        """Template-based fallback when no LLM is available."""
        base = profile.current_description or f"Tool: {profile.tool_name}"
        top_codes = sorted(profile.error_codes.items(), key=lambda kv: kv[1], reverse=True)
        code_hints = ", ".join(c for c, _ in top_codes[:2]) if top_codes else ""
        suffix = ""
        if code_hints:
            suffix = f" Avoid inputs that cause: {code_hints}."
        proposed = (base + suffix)[: SkillEvolver.MAX_DESCRIPTION_CHARS]
        rationale = (
            f"Added error avoidance hint for {profile.failure_count} observed failures"
            + (f" (codes: {code_hints})" if code_hints else "")
            + "."
        )
        return proposed, rationale

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_proposals(self, proposals: List[SkillEvolutionProposal]) -> None:
        """Append proposals to the persistent proposals file."""
        self._base.mkdir(parents=True, exist_ok=True)
        with open(self._proposals_path, "a", encoding="utf-8") as fh:
            for prop in proposals:
                fh.write(json.dumps(prop.to_dict(), ensure_ascii=False) + "\n")

    def list_proposals(
        self,
        *,
        status: Optional[str] = None,
        tool_name: Optional[str] = None,
        limit: int = 50,
    ) -> List[SkillEvolutionProposal]:
        items = self._read_proposals()
        if status:
            status_key = str(status).strip().lower()
            items = [p for p in items if p.status == status_key]
        if tool_name:
            tool_key = str(tool_name).strip().lower()
            items = [p for p in items if p.tool_name == tool_key]
        items.sort(key=lambda p: p.created_at, reverse=True)
        return items[:max(1, int(limit))]

    def get_proposal(self, proposal_id: str) -> Optional[SkillEvolutionProposal]:
        target = str(proposal_id).strip()
        for p in self._read_proposals():
            if p.proposal_id == target:
                return p
        return None

    def approve_proposal(
        self,
        proposal_id: str,
        *,
        actor: str,
        note: str = "",
    ) -> Optional[SkillEvolutionProposal]:
        return self._update_status(
            proposal_id, status="approved", actor=actor, note=note
        )

    def reject_proposal(
        self,
        proposal_id: str,
        *,
        actor: str,
        note: str = "",
    ) -> Optional[SkillEvolutionProposal]:
        return self._update_status(
            proposal_id, status="rejected", actor=actor, note=note
        )

    def apply_proposal(
        self,
        proposal_id: str,
        *,
        actor: str,
        note: str = "",
    ) -> Dict[str, Any]:
        """Apply an approved proposal to the skill_overrides config section.

        Only approved proposals may be applied.  The change is written to the
        ``skill_overrides.<tool_name>.description`` config path and does NOT
        modify any Python source files.
        """
        prop = self.get_proposal(proposal_id)
        if prop is None:
            raise ValueError(f"Proposal '{proposal_id}' not found")
        if prop.status != "approved":
            raise ValueError(
                f"Proposal '{proposal_id}' has status '{prop.status}'; "
                "only 'approved' proposals can be applied"
            )

        # Write to config skill_overrides
        config_path = f"skill_overrides.{prop.tool_name}.description"
        try:
            from tools.admin.state import config as _cfg
            _cfg.set(config_path, prop.proposed_description)
            _cfg.save()
        except Exception as exc:
            raise RuntimeError(f"Failed to write skill override config: {exc}") from exc

        self._update_status(proposal_id, status="applied", actor=actor, note=note)

        return {
            "applied": True,
            "proposal_id": proposal_id,
            "tool_name": prop.tool_name,
            "config_path": config_path,
            "description_before": prop.current_description,
            "description_after": prop.proposed_description,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_proposals(self) -> List[SkillEvolutionProposal]:
        if not self._proposals_path.is_file():
            return []
        results: List[SkillEvolutionProposal] = []
        try:
            for line in self._proposals_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    results.append(SkillEvolutionProposal.from_dict(data))
        except Exception:
            return []
        return results

    def _write_proposals(self, proposals: List[SkillEvolutionProposal]) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        with open(self._proposals_path, "w", encoding="utf-8") as fh:
            for prop in proposals:
                fh.write(json.dumps(prop.to_dict(), ensure_ascii=False) + "\n")

    def _update_status(
        self,
        proposal_id: str,
        *,
        status: str,
        actor: str,
        note: str,
    ) -> Optional[SkillEvolutionProposal]:
        target = str(proposal_id).strip()
        proposals = self._read_proposals()
        found: Optional[SkillEvolutionProposal] = None
        for p in proposals:
            if p.proposal_id == target:
                p.status = status
                p.updated_at = time.time()
                p.actor = str(actor or "")
                p.note = str(note or "")
                found = p
                break
        if found is None:
            return None
        self._write_proposals(proposals)
        return found

    @staticmethod
    def _extract_events(item: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract tool-result events from either raw trajectory or bridge sample."""
        # Bridge sample format: item["tool_result"]["events"]
        tr = item.get("tool_result")
        if isinstance(tr, dict):
            evts = tr.get("events")
            if isinstance(evts, list):
                return evts
        # Raw trajectory format: item["events"] filtered by action == "tool_result"
        raw_events = item.get("events")
        if isinstance(raw_events, list):
            results = []
            for ev in raw_events:
                if not isinstance(ev, dict):
                    continue
                if str(ev.get("action", "")).strip().lower() != "tool_result":
                    continue
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                results.append(
                    {
                        "tool": str(payload.get("tool", ev.get("tool", ""))).strip(),
                        "status": str(payload.get("status", ev.get("status", ""))).strip().lower(),
                        "error_code": str(payload.get("error_code", ev.get("error_code", ""))).strip(),
                    }
                )
            return results
        return []
