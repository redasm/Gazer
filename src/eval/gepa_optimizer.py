"""GEPA-Lite: Genetic-Pareto Prompt Evolution optimizer.

Operates on top of the rule-based ``LightningLiteTrainer`` seed patch.
Runs a micro-population evolutionary search (no GPU, no LLM calls) to find
prompt/policy/router patches with higher fitness than the seed.

Algorithm overview:
  1. Seed: rule-based patch from LightningLiteTrainer becomes generation-0 candidate.
  2. Populate: expand seed to ``population_size`` via random mutation.
  3. Evaluate: score each candidate with the fitness function.
  4. Select: keep top ``elite_ratio`` fraction as parents (elitism).
  5. Reproduce: fill remaining slots via crossover + mutation.
  6. Repeat for ``generations`` iterations.
  7. Return: best candidate + Pareto-front approximation.

Fitness dimensions:
  - eval_pass_improvement  (weight 0.50): how many more eval_samples would pass
                                          under this patch's rules vs no patch
  - rule_parsimony         (weight 0.20): fewer rules = less over-specification
  - tool_coverage          (weight 0.20): deny rules cover observed failing tools
  - router_alignment       (weight 0.10): router strategy matches error distribution
"""

from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Optional, Tuple

_ERROR_MARKERS = frozenset(
    {"error:", "sorry, i couldn", "timed out", "failed", "not permitted"}
)

# Extended rule pool: populated at runtime from trajectory error signals
_STATIC_RULE_POOL: List[str] = [
    "Refuse unsafe or destructive requests and suggest safer alternatives.",
    "Before tool invocation, verify goal-tool alignment in one short sentence.",
    "When uncertain, ask one clarifying question before proceeding.",
    "Prefer lightweight fallback plan when provider/network instability is detected.",
    "Do not repeat non-retryable actions; return explicit recovery steps.",
    "For repeated tool failures, switch to an alternative tool or return a deterministic fallback.",
    "When policy denies a tool, stop retrying and explain allowed alternatives immediately.",
    "Summarise what was accomplished before returning a partial result.",
    "Validate required parameters before calling any tool.",
    "When a task spans multiple steps, confirm intermediate results before proceeding.",
    "Prefer idempotent operations when retrying after an error.",
    "Escalate to the user when two consecutive tool attempts for the same goal fail.",
]

_ROUTER_STRATEGIES = ["cost", "latency", "priority", "availability"]
_ROUTER_TEMPLATES = [
    "cost_first",
    "latency_first",
    "availability_first",
    "balanced",
]


def _looks_like_error(text: str) -> bool:
    content = str(text or "").strip().lower()
    if not content:
        return True
    return any(marker in content for marker in _ERROR_MARKERS)


def _extract_dynamic_rules(
    trajectory_samples: List[Dict[str, Any]],
) -> List[str]:
    """Derive candidate rules from trajectory error codes and feedback text.

    These supplement the static pool and are specific to the current batch.
    """
    rules: List[str] = []
    seen: set = set()
    for item in trajectory_samples:
        events = item.get("events") if isinstance(item.get("events"), list) else []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if str(ev.get("action", "")).strip().lower() != "tool_result":
                continue
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            code = str(payload.get("error_code", "")).strip().lower()
            tool = str(payload.get("tool", "")).strip().lower()
            if code and tool and code not in seen:
                seen.add(code)
                rules.append(
                    f"When {tool} returns {code}, provide an explicit recovery suggestion."
                )
        fb = str(item.get("feedback", "")).strip().lower()
        if "slow" in fb or "latency" in fb:
            r = "Prefer low-latency tools when response speed is important."
            if r not in seen:
                seen.add(r)
                rules.append(r)
        if "context" in fb and ("lost" in fb or "forgot" in fb):
            r = "Recap relevant prior context before starting a multi-step task."
            if r not in seen:
                seen.add(r)
                rules.append(r)
    return rules


def _score_rules_against_eval(
    rules: List[str],
    eval_samples: List[Dict[str, Any]],
) -> float:
    """Estimate pass-rate uplift from adding rules to the system prompt.

    Uses a simple heuristic: a rule with keywords matching an eval sample's
    error text is likely to prevent that error.
    """
    if not eval_samples:
        return 0.0
    token_re = re.compile(r"[a-zA-Z0-9_]+")
    rule_tokens: set = set()
    for rule in rules:
        rule_tokens.update(t.lower() for t in token_re.findall(rule))

    helped = 0
    for item in eval_samples:
        output = str(item.get("reference_output", item.get("assistant_output", ""))).lower()
        if _looks_like_error(output):
            # Check if any rule token appears in the error text
            output_tokens = set(t.lower() for t in token_re.findall(output))
            if rule_tokens & output_tokens:
                helped += 1
    return helped / len(eval_samples)


class GEPAOptimizer:
    """Genetic-Pareto Prompt Evolution optimizer (GEPA-Lite).

    All randomness is seeded for reproducibility. Pass a different ``seed``
    per training job to get independent explorations.
    """

    def __init__(
        self,
        *,
        population_size: int = 12,
        generations: int = 8,
        mutation_rate: float = 0.35,
        elite_ratio: float = 0.25,
        seed: int = 42,
    ) -> None:
        self.population_size = max(4, int(population_size))
        self.generations = max(2, int(generations))
        self.mutation_rate = max(0.05, min(0.95, float(mutation_rate)))
        self.elite_ratio = max(0.1, min(0.9, float(elite_ratio)))
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # Mutation operators
    # ------------------------------------------------------------------

    def mutate_add_rule(
        self,
        patch: Dict[str, Any],
        rule_pool: List[str],
    ) -> Dict[str, Any]:
        """Add one rule from the pool that is not already present."""
        if not rule_pool:
            return patch
        current = set(patch.get("prompt_patch", {}).get("rules", []))
        candidates = [r for r in rule_pool if r not in current]
        if not candidates:
            return patch
        chosen = self._rng.choice(candidates)
        new_rules = sorted(current | {chosen})
        return self._replace_rules(patch, new_rules)

    def mutate_remove_rule(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Remove one rule at random; always keeps at least one rule."""
        rules = list(patch.get("prompt_patch", {}).get("rules", []))
        if len(rules) <= 1:
            return patch
        idx = self._rng.randrange(len(rules))
        new_rules = rules[:idx] + rules[idx + 1:]
        return self._replace_rules(patch, new_rules)

    def mutate_router_strategy(
        self,
        patch: Dict[str, Any],
        error_buckets: Dict[str, int],
    ) -> Dict[str, Any]:
        """Adjust router strategy based on observed error distribution."""
        retryable = int(error_buckets.get("retryable", 0))
        non_retryable = int(error_buckets.get("non_retryable", 0))
        total = retryable + non_retryable

        if total == 0:
            strategy = self._rng.choice(_ROUTER_STRATEGIES)
            template = self._rng.choice(_ROUTER_TEMPLATES)
        elif retryable > non_retryable:
            strategy = "latency"
            template = "availability_first"
        else:
            strategy = "priority"
            template = "latency_first"

        new_patch = dict(patch)
        rp = dict(new_patch.get("router_patch") or {})
        rp["strategy"] = strategy
        rp["strategy_template"] = template
        new_patch["router_patch"] = rp
        return new_patch

    def crossover(
        self,
        parent_a: Dict[str, Any],
        parent_b: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Produce a child by taking the first half of A's rules and second half of B's."""
        rules_a = list(parent_a.get("prompt_patch", {}).get("rules", []))
        rules_b = list(parent_b.get("prompt_patch", {}).get("rules", []))

        mid_a = max(1, len(rules_a) // 2)
        mid_b = len(rules_b) // 2
        combined = rules_a[:mid_a] + rules_b[mid_b:]
        # Deduplicate while preserving order
        seen: set = set()
        unique: List[str] = []
        for r in combined:
            if r not in seen:
                seen.add(r)
                unique.append(r)

        child = dict(parent_a)
        child["prompt_patch"] = dict(parent_a.get("prompt_patch") or {})
        child["prompt_patch"]["rules"] = sorted(unique)
        # Router patch: randomly inherit from one parent
        if self._rng.random() < 0.5 and parent_b.get("router_patch"):
            child["router_patch"] = dict(parent_b["router_patch"])
        return child

    # ------------------------------------------------------------------
    # Fitness function
    # ------------------------------------------------------------------

    def score_candidate(
        self,
        patch: Dict[str, Any],
        trajectory_samples: List[Dict[str, Any]],
        eval_samples: List[Dict[str, Any]],
    ) -> float:
        """Compute a scalar fitness score in [0, 1]."""
        rules = list(patch.get("prompt_patch", {}).get("rules", []))

        # Dimension 1: eval pass improvement (0.50)
        eval_score = _score_rules_against_eval(rules, eval_samples)

        # Dimension 2: rule parsimony — fewer rules preferred (0.20)
        max_rules = max(1, len(_STATIC_RULE_POOL))
        parsimony = max(0.0, 1.0 - (len(rules) / max_rules))

        # Dimension 3: tool coverage — deny rules cover observed failing tools (0.20)
        deny_rules = set(patch.get("policy_patch", {}).get("security.tool_denylist.add", []))
        failing_tools: set = set()
        for item in trajectory_samples:
            events = item.get("events") if isinstance(item.get("events"), list) else []
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                if str(ev.get("action", "")).strip().lower() != "tool_result":
                    continue
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                status = str(payload.get("status", "")).strip().lower()
                tool = str(payload.get("tool", "")).strip().lower()
                if status not in {"ok", "success"} and tool:
                    failing_tools.add(tool)
        tool_coverage = (
            len(deny_rules & failing_tools) / len(failing_tools)
            if failing_tools else 1.0
        )

        # Dimension 4: router alignment (0.10)
        rp = patch.get("router_patch", {})
        router_strategy = str(rp.get("strategy", "cost")).strip().lower()
        budget = rp.get("budget", {}) if isinstance(rp.get("budget"), dict) else {}
        router_score = 1.0 if router_strategy in {"priority", "latency"} else 0.5
        if budget.get("prefer_healthy_provider") and failing_tools:
            router_score = min(1.0, router_score + 0.3)

        fitness = (
            0.50 * eval_score
            + 0.20 * parsimony
            + 0.20 * tool_coverage
            + 0.10 * router_score
        )
        return round(min(1.0, max(0.0, fitness)), 6)

    # ------------------------------------------------------------------
    # Evolution main loop
    # ------------------------------------------------------------------

    def evolve(
        self,
        seed_patch: Dict[str, Any],
        trajectory_samples: List[Dict[str, Any]],
        eval_samples: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Run the evolutionary loop and return results.

        Returns::

            {
              "best_patch": {...},
              "pareto_front": [...],
              "generation_scores": [float, ...],   # best score per generation
              "generations_run": int,
            }
        """
        # Build rule pool: static + dynamic from this batch
        dynamic_rules = _extract_dynamic_rules(trajectory_samples)
        rule_pool = list(dict.fromkeys(_STATIC_RULE_POOL + dynamic_rules))

        # Resolve error buckets for router mutation
        error_buckets = self._extract_error_buckets(trajectory_samples)

        # --- Generation 0: expand seed to population_size ---
        population: List[Tuple[Dict, float]] = []
        seed_score = self.score_candidate(seed_patch, trajectory_samples, eval_samples)
        population.append((seed_patch, seed_score))

        for _ in range(self.population_size - 1):
            candidate = self._random_mutant(seed_patch, rule_pool, error_buckets)
            score = self.score_candidate(candidate, trajectory_samples, eval_samples)
            population.append((candidate, score))

        generation_scores: List[float] = [max(s for _, s in population)]

        # --- Evolution loop ---
        for _gen in range(self.generations):
            population.sort(key=lambda t: t[1], reverse=True)
            n_elite = max(1, int(self.population_size * self.elite_ratio))
            elites = population[:n_elite]

            offspring: List[Tuple[Dict, float]] = []
            while len(offspring) < self.population_size - n_elite:
                pa, _ = self._rng.choice(elites)
                pb, _ = self._rng.choice(elites)
                child = self.crossover(pa, pb)
                # Apply mutation with probability mutation_rate
                if self._rng.random() < self.mutation_rate:
                    child = self._random_mutant(child, rule_pool, error_buckets)
                score = self.score_candidate(child, trajectory_samples, eval_samples)
                offspring.append((child, score))

            population = elites + offspring
            population.sort(key=lambda t: t[1], reverse=True)
            generation_scores.append(round(population[0][1], 6))

        # --- Results ---
        best_patch, best_score = population[0]
        pareto_front = self._extract_pareto_front(population, trajectory_samples, eval_samples)

        return {
            "best_patch": best_patch,
            "best_score": best_score,
            "pareto_front": [p for p, _ in pareto_front],
            "generation_scores": generation_scores,
            "generations_run": self.generations,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _replace_rules(patch: Dict[str, Any], rules: List[str]) -> Dict[str, Any]:
        new_patch = dict(patch)
        new_patch["prompt_patch"] = dict(patch.get("prompt_patch") or {})
        new_patch["prompt_patch"]["rules"] = rules
        return new_patch

    def _random_mutant(
        self,
        patch: Dict[str, Any],
        rule_pool: List[str],
        error_buckets: Dict[str, int],
    ) -> Dict[str, Any]:
        ops = [
            lambda p: self.mutate_add_rule(p, rule_pool),
            lambda p: self.mutate_remove_rule(p),
            lambda p: self.mutate_router_strategy(p, error_buckets),
        ]
        chosen_op = self._rng.choice(ops)
        return chosen_op(patch)

    @staticmethod
    def _extract_error_buckets(
        trajectory_samples: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        buckets: Dict[str, int] = {}
        try:
            from runtime.resilience import classify_error_message
        except ImportError:
            return buckets
        for item in trajectory_samples:
            output = str(item.get("assistant_output", ""))
            reason = classify_error_message(output)
            buckets[reason] = buckets.get(reason, 0) + 1
        return buckets

    def _extract_pareto_front(
        self,
        population: List[Tuple[Dict, float]],
        trajectory_samples: List[Dict[str, Any]],
        eval_samples: List[Dict[str, Any]],
    ) -> List[Tuple[Dict, float]]:
        """Return a simple Pareto-front approximation on (eval_pass, parsimony)."""
        scored: List[Tuple[Dict, float, float]] = []  # (patch, eval_score, parsimony)
        for patch, _total in population:
            rules = list(patch.get("prompt_patch", {}).get("rules", []))
            e = _score_rules_against_eval(rules, eval_samples)
            p = max(0.0, 1.0 - len(rules) / max(1, len(_STATIC_RULE_POOL)))
            scored.append((patch, e, p))

        front: List[Tuple[Dict, float]] = []
        for i, (patch_i, e_i, p_i) in enumerate(scored):
            dominated = False
            for j, (_, e_j, p_j) in enumerate(scored):
                if i == j:
                    continue
                if e_j >= e_i and p_j >= p_i and (e_j > e_i or p_j > p_i):
                    dominated = True
                    break
            if not dominated:
                total_score = self.score_candidate(patch_i, trajectory_samples, eval_samples)
                front.append((patch_i, total_score))

        return front
