# Gazer: batch commits for open-source release
# Run from repo root: .\scripts\git_batch_commits.ps1
$ErrorActionPreference = "Stop"
$git = "C:\Users\nave\AppData\Local\Atlassian\SourceTree\git_local\cmd\git.exe"
$root = "e:\AppProject\Gazer"
Set-Location $root

# Init and remote (idempotent)
if (-not (Test-Path .git)) {
  & $git init
  & $git checkout -b main
  & $git remote add origin git@github.com:redasm/Gazer.git
  Write-Host "Initialized repo and remote."
}

function Commit {
  param([string]$msg, [string[]]$paths)
  foreach ($p in $paths) {
    if (Test-Path $p) { & $git add $p }
  }
  $count = (& $git diff --cached --name-only | Measure-Object).Count
  if ($count -eq 0) { Write-Host "  (skip empty)" ; return }
  & $git commit -m $msg
  if ($LASTEXITCODE -ne 0) { throw "commit failed: $msg" }
  Write-Host "  committed $count files"
}

# 1. Project scaffold
Write-Host "1. scaffold"
Commit "chore: add project scaffold and dependencies" @(
  "LICENSE", "README.md", ".gitignore", ".dockerignore", ".env.example",
  "AGENTS.md", "CLAUDE.md", "requirements.txt", "constraints.txt", "pyproject.toml", ".gitmodules", ".windsurfrules"
)

# 2. Config
Write-Host "2. config"
Commit "chore: add default configuration" @("config/settings.yaml")

# 3. Runtime
Write-Host "3. runtime"
Commit "feat(runtime): add brain, config manager, IPC, provider registry" @("main.py", "src/runtime")

# 4. Bus
Write-Host "4. bus"
Commit "feat(bus): add message bus and events" @("src/bus")

# 5. LLM
Write-Host "5. llm"
Commit "feat(llm): add router, LiteLLM provider, prompt cache" @("src/llm")

# 6. Memory
Write-Host "6. memory"
Commit "feat(memory): add OpenViking backend and memory manager" @("src/memory")

# 7. Soul + assets
Write-Host "7. soul + assets"
Commit "feat(soul): add personality, cognitive, working context and assets" @("src/soul", "assets")

# 8. Agent
Write-Host "8. agent"
Commit "feat(agent): add agent loop, orchestrator, adapter, policy pipeline" @("src/agent")

# 9. Multi-agent
Write-Host "9. multi-agent"
Commit "feat(multi-agent): add planner, workers, task graph, brain router" @("src/multi_agent")

# 10. Tools
Write-Host "10. tools"
Commit "feat(tools): add registry, admin API, coding and device tools" @("src/tools")

# 11. Channels
Write-Host "11. channels"
Commit "feat(channels): add web, telegram, discord, slack, feishu adapters" @("src/channels")

# 12. Devices
Write-Host "12. devices"
Commit "feat(devices): add registry, local desktop and satellite adapters" @("src/devices")

# 13. Security
Write-Host "13. security"
Commit "feat(security): add owner, pairing, threat scan, file crypto" @("src/security")

# 14. Plugins, flow, eval, cli, extensions
Write-Host "14. plugins/flow/eval/cli/extensions"
Commit "feat: add plugins, flow interop, eval, CLI, desktop extension" @("src/plugins", "src/flow", "src/eval", "src/cli", "src/extensions")

# 15. Docs
Write-Host "15. docs"
Commit "docs: add architecture, modules, getting started and reference" @("docs", "doc")

# 16. Tests
Write-Host "16. tests"
Commit "test: add pytest suite for agent, tools, memory, security" @("tests")

# 17. Web frontend
Write-Host "17. web"
Commit "feat(web): add React admin console and pages" @("web")

# 18. Docker, scripts, design
Write-Host "18. docker, scripts"
Commit "chore: add Docker, scripts and remaining assets" @(
  "Dockerfile", "docker-compose.yml", "scripts", "design", "perception", "satellite", "skills", "workflows", "ui", "tools", "electronics", "hardware"
)

Write-Host "Done. Push with: git push -u origin main"
& $git log --oneline -25
