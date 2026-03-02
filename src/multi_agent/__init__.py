"""Multi-Agent Collaboration System for Gazer.

Implements an orchestrator-worker pattern with:
- BrainHint + DualBrainRouter (three-dimension brain routing)
- TaskComplexityAssessor (four-dimension scoring for agent allocation)
- TaskGraph (DAG-based task management)
- AgentMessageBus (inter-agent communication)
- Blackboard (shared state via OpenViking)
- WorkerAgent (lightweight agents with work stealing)
- AgentPool (dynamic scaling)
- PlannerAgent (planning, monitoring, aggregation)
- MultiAgentRuntime (unified entry point)
"""
