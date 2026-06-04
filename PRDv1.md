Agent Session Workboard 项目设计文档

0. 项目一句话目标

做一个本地优先、低权限、只读为主的 Agent Session Workboard，用于汇总 Codex CLI / Claude Code / Cursor / tmux / git worktree / 远程服务器上的并行工作线，帮助用户快速回答：

1. 我现在有哪些还没收尾的工作线？
2. 每条线当前进展到哪里？
3. 下一步应该做什么？
4. 哪些 session 值得继续，哪些应该 archive，哪些应该 fork 一个新 session？
5. 如何一键跳转回对应机器、目录、worktree、tmux pane 或 agent session？

第一版不追求企业级产品，不做复杂权限系统，不做完整 IDE，不替代 Codex / Claude Code / Cursor，而是做一个类似 Karpathy autoresearch 风格的开源原型：repo clone 下来能跑，架构清晰，能处理真实个人工作流，有 Web UI 可视化。

⸻

1. 背景与痛点

用户在日常使用 Codex CLI、ChatGPT/Codex App、Claude Code、Cursor、远程服务器、tmux、git worktree 时，经常会同时开启多个 session。这些 session 可能按以下维度分散：

* 不同机器：本地 Mac、远程 H200、远程 Ascend、云服务器；
* 不同项目：多个 repo、多套实验环境；
* 不同工具：Codex CLI、Claude Code、Cursor、ChatGPT、tmux shell；
* 不同 thread/session：同一个任务可能被多次 resume、compact、fork、重试；
* 不同 git worktree/branch：每个 agent 可能在独立 worktree 中修改代码。

随着并行任务变多，用户很容易失去对全局状态的掌控：

* 忘记某个 session 的原始目标；
* 不知道某个 worktree 当前是否还值得继续；
* 不知道哪个 session 已经跑偏；
* 不知道某个分支是否包含有价值改动；
* 不知道 compact 前后的关键上下文是否保留；
* 不知道该 resume 旧 session 还是新开一个干净 session；
* 多个工具之间没有统一的记忆库和跳转入口。

已有工具通常只解决局部问题：

* Codex App 管 Codex 自己的 thread/worktree；
* Claude Code 管 Claude 自己的 session；
* Vibe Kanban / Nimbalyst 更偏 agent workspace / worktree manager；
* ccmanager 更偏 Claude Code + worktree 的 TUI 管理；
* History Viewer 类工具偏历史浏览，不负责判断“下一步做什么”。

本项目要解决的不是“启动更多 agent”，而是做一个外部认知层：

把散落在不同机器、工具、session、worktree 中的工作进展整理成一个可检索、可跳转、可决策的任务记忆库。

⸻

2. 产品定位

2.1 项目名称

暂定：agent-session-workboard

也可叫：

* agentboard
* worktree-brain
* session-ledger
* agent-work-ledger

本文档中统一称为 Agent Session Workboard，命令行工具名暂定为 agentboard。

2.2 核心定位

Agent Session Workboard 是一个：

* 本地优先的；
* 只读为主的；
* 支持多工具、多机器、多 repo 的；
* session / worktree / task 状态索引器；
* 带有 Web UI 的个人 agent 工作总控台。

它不是：

* 不是新的 coding agent；
* 不是 IDE；
* 不是完整 agent orchestration 平台；
* 不是多用户 SaaS；
* 不是 Claude Code / Codex / Cursor 的替代品；
* 第一版不主动控制 agent 执行，不自动 merge，不自动删除 worktree。

2.3 第一版目标

第一版目标是让用户运行：

uv sync
uv run agentboard init
uv run agentboard scan
uv run agentboard summarize
uv run agentboard web

然后打开：

http://localhost:8765

可以看到一个 Dashboard：

Active Work Lines: 14
- 4 active
- 3 blocked
- 2 ready for review
- 3 stale
- 2 should fork new session

每条工作线展示：

* 机器名；
* 项目/repo；
* worktree/branch；
* agent 类型；
* 原始目标；
* 当前状态；
* 下一步；
* 最近活动时间；
* 重要文件；
* 重要错误；
* 风险提示；
* 跳转命令；
* 是否建议继续 / fork / archive。

⸻

3. MVP 范围

3.1 必做功能

MVP 必须支持以下功能。

3.1.1 本地配置初始化

提供命令：

uv run agentboard init

生成：

.agentboard/
  config.yaml
  agentboard.db
  logs/

或默认使用：

~/.agentboard/config.yaml
~/.agentboard/agentboard.db

配置文件示例：

workspace:
  db_path: ~/.agentboard/agentboard.db
  data_dir: ~/.agentboard
machines:
  - name: local
    type: local
    roots:
      - ~/projects
      - ~/work
    codex_home: ~/.codex
    claude_home: ~/.claude
    tmux: true
llm:
  provider: openai_compatible
  model: deepseek-reasoner
  base_url: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
redaction:
  enabled: true
  exclude_paths:
    - .env
    - auth.json
    - id_rsa
    - id_ed25519

3.1.2 Git collector

采集 git repo/worktree 信息。

需要支持：

git worktree list --porcelain
git status --short
git branch --show-current
git log -n 5 --oneline
git diff --stat
git diff --name-only

输出统一事件或实体：

* repo path；
* git remote；
* branch；
* worktree path；
* dirty files；
* changed files；
* recent commits；
* diff stat；
* last modified time。

Git 是最可靠的事实来源，优先级高于 LLM session 摘要。

3.1.3 Codex session collector

扫描本地 Codex session 日志。

默认路径：

~/.codex/sessions/**/*.jsonl

要求：

* parser 必须宽松；
* 解析失败不能导致整个 scan 崩溃；
* 保存 raw event；
* 尽量提取 timestamp、role、text、tool_call、cwd、session_id；
* 如果字段格式未知，保留 raw JSON。

3.1.4 Claude Code session collector

扫描 Claude Code transcript。

可能路径：

~/.claude/projects/**/*.jsonl

或从配置中读取。

要求：

* parser 宽松；
* 保存 raw event；
* 提取 session_id、cwd、timestamp、role、message、tool call、file edit、command 等可用信息；
* 后续可以接入 Claude Code hooks，但 MVP 可以先不做 hook。

3.1.5 tmux collector

采集 tmux session/window/pane 状态。

命令：

tmux list-sessions
tmux list-windows -a
tmux list-panes -a -F '#{session_name}|#{window_index}|#{pane_index}|#{pane_current_path}|#{pane_current_command}|#{pane_active}'
tmux capture-pane -p -t <target>

要求：

* 显示 tmux session name；
* window/pane；
* current path；
* current command；
* pane 最近屏幕内容摘要；
* 生成 jump command，例如：

tmux attach -t <session>

或：

tmux select-window -t <session>:<window>

3.1.6 统一事件模型

把不同来源的信息统一成 event/entity。

示例：

{
  "event_id": "evt_...",
  "source": "codex|claude|git|tmux|manual",
  "machine": "local",
  "repo_path": "/Users/asher/projects/foo",
  "worktree_path": "/Users/asher/projects/foo-wt/bar",
  "session_id": "...",
  "timestamp": "2026-05-27T10:00:00+09:00",
  "event_type": "message|tool_call|shell_command|file_edit|git_status|error|test_result|compact|unknown",
  "summary": "short human-readable summary",
  "content": "raw or normalized content",
  "raw": {},
  "metadata": {}
}

3.1.7 Task / Work Line 聚类

MVP 中不需要复杂算法，但要有基础聚类。

优先规则：

1. 同 machine + 同 repo + 同 worktree = 同 work line；
2. 同 machine + 同 repo + 同 branch = 可能同 work line；
3. session cwd 落在某个 repo/worktree 下，则关联该 repo/worktree；
4. tmux pane current path 落在某个 repo/worktree 下，则关联；
5. 如果无法关联，创建 standalone session work line。

后续再加：

* goal embedding；
* touched files similarity；
* error string similarity；
* command history similarity；
* manual merge/split。

3.1.8 State Card 生成

每条 work line 生成结构化 state card。

字段：

{
  "workline_id": "wl_...",
  "title": "short title",
  "goal": "original or inferred task goal",
  "status": "active|blocked|review|done|stale|unknown",
  "health": "continue|fork|archive|review|unknown",
  "last_known_good_state": "...",
  "current_state": "...",
  "next_action": "...",
  "blocked_reason": "...",
  "important_files": [],
  "important_errors": [],
  "avoid_repeating": [],
  "open_questions": [],
  "jump_targets": [],
  "evidence_event_ids": [],
  "confidence": 0.0,
  "updated_at": "..."
}

状态定义：

* active: 最近有进展，下一步明确；
* blocked: 有明确错误/阻塞；
* review: 有代码改动，需要用户 review；
* done: 明确完成；
* stale: 长时间无活动或状态不清；
* unknown: 信息不足。

Health 定义：

* continue: 可以继续旧 session；
* fork: 建议新开干净 session；
* archive: 建议归档；
* review: 建议先 review diff 或结果；
* unknown: 无法判断。

3.1.9 LLM 状态抽取

MVP 支持 OpenAI-compatible API。

命令：

uv run agentboard summarize
uv run agentboard summarize --workline <id>

输入：

* 该 work line 最近 N 条关键事件；
* git status/diff stat；
* 最近 commits；
* 上一次 state card；
* tmux pane summary；
* session last messages。

输出：

* 严格 JSON state card。

要求：

* 不允许自由格式输出；
* JSON schema 校验；
* 失败时保留原 state card；
* 不确定字段写 unknown 或空数组；
* 必须引用 evidence_event_ids；
* 必须区分旧计划和当前状态；
* 必须尽量输出 next_action；
* 必须输出 avoid_repeating，尤其是已失败的重复尝试。

3.1.10 Seed Prompt 生成

为一个 work line 生成“新 session 接续 prompt”。

命令：

uv run agentboard seed <workline_id>

输出类似：

You are continuing an existing coding task.
Goal:
...
Current state:
...
Last known good state:
...
Important files:
...
Important errors:
...
Do not repeat:
...
Next action:
...
Before making changes, inspect:
...

Web UI 中也要提供复制按钮。

3.1.11 Web UI

提供命令：

uv run agentboard web

默认打开：

http://localhost:8765

页面包括：

1. Dashboard；
2. Work Line Detail；
3. Sessions；
4. Worktrees；
5. Seed Prompt。

Web UI 技术建议：

* FastAPI；
* Jinja2；
* HTMX；
* Alpine.js 可选；
* Tailwind 可选，但不要引入复杂前端构建系统。

第一版尽量避免 React/Vite，降低 clone 后运行成本。

3.1.12 Demo Mode

必须提供 demo 数据和 demo 命令。

uv run agentboard demo
uv run agentboard web --demo

demo 数据放在：

examples/
  codex_short.jsonl
  codex_long_drift.jsonl
  claude_compact.jsonl
  git_status_sample.json
  tmux_capture_sample.txt

用户 clone 下来即使没有真实 Codex/Claude 日志，也能打开 Web UI 看到效果。

⸻

4. 非 MVP 范围

第一版明确不做：

* 不做多用户登录；
* 不做云同步；
* 不做团队权限；
* 不做自动 merge；
* 不做自动删除 worktree；
* 不做主动控制 Codex/Claude Code 执行；
* 不做 Cursor 私有数据深度解析；
* 不做浏览器插件；
* 不做 VS Code 插件；
* 不做复杂向量数据库；
* 不做完整 agent orchestration；
* 不保证所有历史 session 完美恢复；
* 不默认上传 raw transcript 到任何外部服务。

⸻

5. 推荐技术栈

5.1 语言与包管理

推荐：

Python 3.11+
uv
Typer
Rich
FastAPI
Jinja2
SQLite
Pydantic

原因：

* AI agent 容易实现；
* JSONL、git、ssh、sqlite 处理方便；
* clone 后运行简单；
* 不需要 Node 前端构建链；
* 适合快速开源原型。

5.2 可选依赖

watchdog       # 文件监听，可后续加入
paramiko       # SSH，可选；MVP 可先 subprocess 调 ssh
textual        # TUI，可选
pytest         # 测试
ruff           # lint

5.3 不建议第一版使用

React/Vite
Postgres
Redis
Celery
Kubernetes
Docker compose 多服务
复杂 vector DB

⸻

6. 项目目录结构

建议结构：

agent-session-workboard/
  README.md
  pyproject.toml
  .gitignore
  .agentboardignore.example
  examples/
    README.md
    codex_short.jsonl
    codex_long_drift.jsonl
    claude_compact.jsonl
    tmux_capture_sample.txt
    demo_config.yaml
  docs/
    architecture.md
    data_model.md
    prompts.md
    supported_tools.md
    development_plan.md
  agentboard/
    __init__.py
    cli.py
    config.py
    db.py
    models.py
    logging.py
    redaction.py
    collectors/
      __init__.py
      base.py
      git.py
      codex.py
      claude.py
      tmux.py
      ssh.py
    normalizer/
      __init__.py
      events.py
      worklines.py
    intelligence/
      __init__.py
      llm.py
      prompts.py
      state_card.py
      health.py
      seed_prompt.py
    web/
      __init__.py
      app.py
      templates/
        base.html
        dashboard.html
        workline_detail.html
        sessions.html
        worktrees.html
        seed_prompt.html
      static/
        style.css
  tests/
    test_git_collector.py
    test_codex_collector.py
    test_claude_collector.py
    test_workline_clustering.py
    test_state_card_schema.py
    test_redaction.py

⸻

7. 数据模型

7.1 Machine

class Machine(BaseModel):
    id: str
    name: str
    type: Literal["local", "ssh"]
    host: str | None = None
    roots: list[str] = []
    codex_home: str | None = None
    claude_home: str | None = None
    tmux_enabled: bool = True

7.2 Repo

class Repo(BaseModel):
    id: str
    machine_id: str
    path: str
    remote_url: str | None = None
    current_branch: str | None = None
    is_git_repo: bool = True
    last_seen_at: datetime

7.3 Worktree

class Worktree(BaseModel):
    id: str
    machine_id: str
    repo_id: str | None
    path: str
    branch: str | None
    head: str | None
    dirty: bool
    dirty_files: list[str]
    changed_files: list[str]
    diff_stat: str | None
    recent_commits: list[str]
    last_seen_at: datetime

7.4 Session

class Session(BaseModel):
    id: str
    source: Literal["codex", "claude", "cursor", "tmux", "manual", "unknown"]
    machine_id: str
    session_key: str
    path: str | None
    cwd: str | None
    repo_id: str | None
    worktree_id: str | None
    started_at: datetime | None
    last_activity_at: datetime | None
    raw_path: str | None
    metadata: dict = {}

7.5 Event

class Event(BaseModel):
    id: str
    source: Literal["codex", "claude", "git", "tmux", "manual", "unknown"]
    machine_id: str
    session_id: str | None
    repo_id: str | None
    worktree_id: str | None
    timestamp: datetime | None
    event_type: Literal[
        "message",
        "tool_call",
        "shell_command",
        "file_edit",
        "git_status",
        "error",
        "test_result",
        "compact",
        "summary",
        "unknown",
    ]
    role: str | None
    summary: str | None
    content: str | None
    raw: dict | None
    metadata: dict = {}

7.6 WorkLine

class WorkLine(BaseModel):
    id: str
    machine_id: str
    repo_id: str | None
    worktree_id: str | None
    title: str
    source_session_ids: list[str]
    status: Literal["active", "blocked", "review", "done", "stale", "unknown"]
    health: Literal["continue", "fork", "archive", "review", "unknown"]
    last_activity_at: datetime | None
    created_at: datetime
    updated_at: datetime

7.7 StateCard

class StateCard(BaseModel):
    id: str
    workline_id: str
    title: str
    goal: str
    status: Literal["active", "blocked", "review", "done", "stale", "unknown"]
    health: Literal["continue", "fork", "archive", "review", "unknown"]
    last_known_good_state: str
    current_state: str
    next_action: str
    blocked_reason: str | None
    important_files: list[str]
    important_errors: list[str]
    avoid_repeating: list[str]
    open_questions: list[str]
    jump_targets: list[str]
    evidence_event_ids: list[str]
    confidence: float
    created_at: datetime
    updated_at: datetime

⸻

8. SQLite Schema

MVP 可使用以下表：

CREATE TABLE machines (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  host TEXT,
  config_json TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE repos (
  id TEXT PRIMARY KEY,
  machine_id TEXT NOT NULL,
  path TEXT NOT NULL,
  remote_url TEXT,
  current_branch TEXT,
  last_seen_at TEXT,
  metadata_json TEXT,
  UNIQUE(machine_id, path)
);
CREATE TABLE worktrees (
  id TEXT PRIMARY KEY,
  machine_id TEXT NOT NULL,
  repo_id TEXT,
  path TEXT NOT NULL,
  branch TEXT,
  head TEXT,
  dirty INTEGER,
  dirty_files_json TEXT,
  changed_files_json TEXT,
  diff_stat TEXT,
  recent_commits_json TEXT,
  last_seen_at TEXT,
  UNIQUE(machine_id, path)
);
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  session_key TEXT NOT NULL,
  path TEXT,
  cwd TEXT,
  repo_id TEXT,
  worktree_id TEXT,
  started_at TEXT,
  last_activity_at TEXT,
  raw_path TEXT,
  metadata_json TEXT,
  UNIQUE(source, machine_id, session_key)
);
CREATE TABLE events (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  session_id TEXT,
  repo_id TEXT,
  worktree_id TEXT,
  timestamp TEXT,
  event_type TEXT,
  role TEXT,
  summary TEXT,
  content TEXT,
  raw_json TEXT,
  metadata_json TEXT
);
CREATE INDEX idx_events_session ON events(session_id);
CREATE INDEX idx_events_worktree ON events(worktree_id);
CREATE INDEX idx_events_timestamp ON events(timestamp);
CREATE TABLE worklines (
  id TEXT PRIMARY KEY,
  machine_id TEXT NOT NULL,
  repo_id TEXT,
  worktree_id TEXT,
  title TEXT NOT NULL,
  source_session_ids_json TEXT,
  status TEXT,
  health TEXT,
  last_activity_at TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE state_cards (
  id TEXT PRIMARY KEY,
  workline_id TEXT NOT NULL,
  title TEXT,
  goal TEXT,
  status TEXT,
  health TEXT,
  last_known_good_state TEXT,
  current_state TEXT,
  next_action TEXT,
  blocked_reason TEXT,
  important_files_json TEXT,
  important_errors_json TEXT,
  avoid_repeating_json TEXT,
  open_questions_json TEXT,
  jump_targets_json TEXT,
  evidence_event_ids_json TEXT,
  confidence REAL,
  created_at TEXT,
  updated_at TEXT
);

可以后续加入 FTS：

CREATE VIRTUAL TABLE events_fts USING fts5(content, summary, content='events', content_rowid='rowid');

⸻

9. CLI 设计

9.1 初始化

agentboard init

行为：

* 创建配置文件；
* 创建 SQLite DB；
* 打印下一步命令。

9.2 扫描

agentboard scan
agentboard scan --machine local
agentboard scan --source git
agentboard scan --source codex
agentboard scan --source claude
agentboard scan --source tmux

行为：

* 读取配置；
* 扫描机器/路径；
* 写入 DB；
* 输出统计信息。

示例输出：

Scan complete.
Machines: 1
Repos: 8
Worktrees: 17
Codex sessions: 24
Claude sessions: 13
Tmux panes: 9
Events indexed: 4218

9.3 列出工作线

agentboard list
agentboard list --status blocked
agentboard list --health fork

示例输出：

ID        Status   Health    Project       Title
wl_001    active   continue  codescout     Compare GRPO/GSPO/SAPO eval scripts
wl_002    blocked  fork      codex-auth    Fix token_revoked on remote machine
wl_003    review   review    resume-css    Tune Typora PDF CSS spacing

9.4 查看详情

agentboard show wl_001

输出：

* state card；
* sessions；
* worktree；
* recent events；
* jump commands。

9.5 生成状态卡

agentboard summarize
agentboard summarize --workline wl_001

9.6 生成接续 prompt

agentboard seed wl_001

9.7 启动 Web UI

agentboard web
agentboard web --port 8765
agentboard web --demo

9.8 Demo

agentboard demo

行为：

* 加载 examples；
* 创建 demo DB；
* 提示运行 web。

⸻

10. Web UI 设计

10.1 Dashboard

路由：

/

模块：

1. Summary cards：
    * total worklines；
    * active；
    * blocked；
    * review；
    * stale；
    * fork suggested。
2. Filter bar：
    * machine；
    * project；
    * status；
    * health；
    * agent source。
3. Workline cards/table：
    * title；
    * status badge；
    * health badge；
    * machine；
    * repo；
    * branch/worktree；
    * next action；
    * last activity；
    * jump command copy button。

10.2 Workline Detail

路由：

/worklines/{id}

展示：

* state card；
* goal；
* current state；
* next action；
* blocked reason；
* avoid repeating；
* important files；
* important errors；
* evidence events；
* related sessions；
* related worktree；
* recent git diff stat；
* seed prompt button。

10.3 Sessions

路由：

/sessions

展示：

* Codex sessions；
* Claude sessions；
* tmux sessions；
* last activity；
* cwd；
* associated workline；
* raw path。

10.4 Worktrees

路由：

/worktrees

展示：

* repo；
* path；
* branch；
* dirty status；
* changed files；
* recent commits；
* associated sessions；
* associated workline。

10.5 Seed Prompt

路由：

/worklines/{id}/seed

展示可复制 prompt。

按钮：

Copy Seed Prompt

⸻

11. LLM Prompt 设计

11.1 State Card Extraction Prompt

系统提示：

You are a state extraction engine for a developer's parallel AI coding sessions.
Your job is not to summarize everything. Your job is to produce a precise, evidence-grounded state card.
Rules:
- Output valid JSON only.
- Do not invent facts.
- If uncertain, use "unknown" or empty arrays.
- Distinguish old plans from the current state.
- Prefer recent evidence, but do not ignore explicit state checkpoints.
- Use git status and diff as strong evidence.
- Include next_action whenever possible.
- Include avoid_repeating for failed or redundant attempts.
- evidence_event_ids must refer to provided events.

用户输入结构：

{
  "previous_state_card": {},
  "workline_context": {
    "machine": "local",
    "repo": "...",
    "worktree": "...",
    "branch": "..."
  },
  "git_context": {
    "dirty_files": [],
    "changed_files": [],
    "diff_stat": "...",
    "recent_commits": []
  },
  "events": [
    {
      "event_id": "evt_1",
      "timestamp": "...",
      "source": "codex",
      "event_type": "message",
      "summary": "...",
      "content": "..."
    }
  ]
}

输出 JSON schema：

{
  "title": "string",
  "goal": "string",
  "status": "active|blocked|review|done|stale|unknown",
  "health": "continue|fork|archive|review|unknown",
  "last_known_good_state": "string",
  "current_state": "string",
  "next_action": "string",
  "blocked_reason": "string|null",
  "important_files": ["string"],
  "important_errors": ["string"],
  "avoid_repeating": ["string"],
  "open_questions": ["string"],
  "jump_targets": ["string"],
  "evidence_event_ids": ["string"],
  "confidence": 0.0
}

11.2 Health Heuristic

LLM 可以参与，但 MVP 应该有规则兜底。

规则：

* 有 dirty files 且最近有成功测试/commit：review；
* 有明显错误且重复出现：fork 或 blocked；
* 长时间无活动且无 dirty files：archive；
* 有明确 next_action 且最近活跃：continue；
* transcript 很长且多次 compact/跑偏：fork；
* 信息不足：unknown。

11.3 Seed Prompt Prompt

根据 StateCard 生成：

You are continuing an existing coding task.
Goal:
{goal}
Current state:
{current_state}
Last known good state:
{last_known_good_state}
Important files:
{important_files}
Important errors:
{important_errors}
Avoid repeating:
{avoid_repeating}
Next action:
{next_action}
Before making changes:
1. Inspect the repo state.
2. Read the important files.
3. Confirm whether the current state still matches this handoff.
4. Then proceed with the next action.

⸻

12. Redaction 与安全

12.1 默认原则

* 默认本地存储；
* 默认不上传 raw transcript；
* LLM 调用前做 secret redaction；
* 用户可以关闭 LLM summarization，只用规则和手动编辑；
* 支持 .agentboardignore。

12.2 必须过滤的内容

* API keys；
* tokens；
* cookies；
* SSH private keys；
* .env；
* auth.json；
* id_rsa；
* id_ed25519；
* *.pem；
* known cloud credentials。

12.3 Redaction 示例

SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{20,}",
    r"ghp_[A-Za-z0-9_]{20,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
    r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]+",
]

替换成：

[REDACTED_SECRET]

⸻

13. 远程机器支持

13.1 MVP 后半段支持

配置：

machines:
  - name: h200
    type: ssh
    host: h200
    roots:
      - /data/projects
      - /mnt/work
    codex_home: ~/.codex
    claude_home: ~/.claude
    tmux: true

实现方式：

* 第一版用系统 ssh 命令；
* 不要求远程安装 daemon；
* 远程执行只读命令；
* 拉取必要 metadata；
* 不默认复制完整 transcript，除非用户配置允许。

命令示例：

ssh h200 'find ~/.codex/sessions -name "*.jsonl" | tail -100'
ssh h200 'git -C /path status --short'
ssh h200 'tmux list-sessions'

13.2 Jump Command

生成：

ssh h200
ssh h200 'tmux attach -t cc-codescout-3'
ssh h200 'cd /data/projects/foo && bash'

Web UI 中提供 copy button。

⸻

14. 测试策略

14.1 单元测试

必须覆盖：

* Codex JSONL parser；
* Claude JSONL parser；
* git collector 输出解析；
* tmux collector 输出解析；
* workline clustering；
* state card JSON schema validation；
* redaction。

14.2 Golden Examples

examples 中每个样本对应 expected：

examples/expected/
  codex_short_state_card.json
  codex_long_drift_state_card.json
  claude_compact_state_card.json

测试时允许文本不完全一致，但关键字段必须匹配：

* status；
* health；
* important_files；
* important_errors；
* next_action 非空；
* evidence_event_ids 非空。

14.3 手动验收

在真实机器上运行：

agentboard init
agentboard scan
agentboard list
agentboard summarize
agentboard web

验收问题：

1. 是否列出了本地主要 repo/worktree？
2. 是否识别了 Codex session？
3. 是否识别了 Claude session？
4. 是否识别了 tmux session？
5. Dashboard 是否能快速看出 active/blocked/review/stale？
6. State card 是否真的能回答“我在哪、下一步做什么”？
7. Seed prompt 是否可以直接复制给新 session？

⸻

15. 开发计划

Phase 0：项目骨架与 Spec

目标：repo clone 下来能安装和运行空命令。

任务：

* 创建 uv 项目；
* 创建 Typer CLI；
* 创建 SQLite 初始化；
* 创建配置文件 loader；
* 创建 README；
* 创建 examples 目录。

验收：

uv sync
uv run agentboard --help
uv run agentboard init

Phase 1：Collectors + DB

目标：能扫描本机基础信息。

任务：

* Git collector；
* Codex collector；
* Claude collector；
* tmux collector；
* event normalizer；
* 写入 SQLite。

验收：

uv run agentboard scan
uv run agentboard list --raw-sessions

Phase 2：Workline 聚类

目标：把 session/worktree/repo 关联成工作线。

任务：

* repo/worktree 匹配；
* session cwd 匹配；
* tmux pane cwd 匹配；
* standalone session workline；
* workline list。

验收：

uv run agentboard list

Phase 3：State Card

目标：为每个 workline 生成结构化状态卡。

任务：

* LLM provider；
* prompt；
* JSON schema；
* state card persistence；
* health heuristic；
* seed prompt。

验收：

uv run agentboard summarize
uv run agentboard show <workline_id>
uv run agentboard seed <workline_id>

Phase 4：Web UI

目标：可视化总控台。

任务：

* FastAPI app；
* Dashboard；
* Workline detail；
* Sessions page；
* Worktrees page；
* Seed prompt page；
* Copy buttons。

验收：

uv run agentboard web

Phase 5：Demo + 文档 + 打磨

目标：开源可用。

任务：

* demo data；
* demo command；
* README screenshots；
* docs；
* tests；
* redaction；
* .agentboardignore。

验收：

git clone <repo>
cd agent-session-workboard
uv sync
uv run agentboard demo
uv run agentboard web --demo

⸻

16. 给 AI Agent 的开发原则

执行本项目时，请遵守：

1. 优先完成可运行闭环，不要过度设计；
2. 每个模块都要有单元测试；
3. Parser 必须宽松，不能因为一条坏日志导致 scan 失败；
4. 所有外部命令必须有 timeout；
5. 所有路径必须展开 ~；
6. 所有 LLM 输出必须 JSON schema 校验；
7. 不要默认上传 raw transcript；
8. 不要读取被 .agentboardignore 排除的路径；
9. Web UI 不要引入复杂前端构建；
10. README 必须始终保持与实际命令一致。

⸻

17. 首批 Issues

Issue 1：Create project skeleton

目标：创建基础 Python/uv 项目。

要求：

* pyproject.toml；
* agentboard/cli.py；
* Typer CLI；
* agentboard --help 可运行；
* README 包含安装和运行说明。

验收：

uv sync
uv run agentboard --help

Issue 2：Implement config and init command

目标：实现配置文件初始化。

要求：

* agentboard init；
* 创建 ~/.agentboard/config.yaml；
* 创建 ~/.agentboard/agentboard.db；
* 支持 --config 参数。

验收：

uv run agentboard init
uv run agentboard init --config ./demo_config.yaml

Issue 3：Implement SQLite schema

目标：实现 DB schema 和基础 DAO。

要求：

* 创建 machines/repos/worktrees/sessions/events/worklines/state_cards 表；
* 提供 upsert 方法；
* 提供 query list 方法。

验收：

pytest tests/test_db.py

Issue 4：Implement Git collector

目标：采集 git repo/worktree 信息。

要求：

* 支持指定 root；
* 自动发现 git repo；
* 运行 git status/log/diff；
* timeout；
* 写入 DB。

验收：

uv run agentboard scan --source git
pytest tests/test_git_collector.py

Issue 5：Implement Codex collector

目标：解析 Codex JSONL session。

要求：

* 默认扫描 ~/.codex/sessions/**/*.jsonl；
* 宽松 parser；
* 保存 raw event；
* 提取 session/event；
* 使用 examples 测试。

验收：

uv run agentboard scan --source codex
pytest tests/test_codex_collector.py

Issue 6：Implement Claude collector

目标：解析 Claude Code transcript。

要求：

* 默认扫描 ~/.claude/projects/**/*.jsonl；
* 宽松 parser；
* 保存 raw event；
* 提取 session/event；
* 使用 examples 测试。

验收：

uv run agentboard scan --source claude
pytest tests/test_claude_collector.py

Issue 7：Implement tmux collector

目标：采集 tmux session/window/pane。

要求：

* 如果 tmux 不存在，不报错；
* 采集 session/window/pane/current_path/current_command；
* 可选 capture-pane；
* 生成 jump command。

验收：

uv run agentboard scan --source tmux
pytest tests/test_tmux_collector.py

Issue 8：Implement workline clustering

目标：把 repo/worktree/session/tmux pane 聚合成 workline。

要求：

* 同 worktree 聚合；
* cwd 匹配 repo/worktree；
* 无法匹配则 standalone；
* 生成 workline title。

验收：

uv run agentboard list
pytest tests/test_workline_clustering.py

Issue 9：Implement LLM provider and state card extraction

目标：调用 OpenAI-compatible API 生成 state card。

要求：

* 从 config 读取 base_url/model/api_key_env；
* 调用 chat completion；
* 强制 JSON 输出；
* Pydantic schema 校验；
* 失败时 graceful fallback。

验收：

uv run agentboard summarize --workline <id>
pytest tests/test_state_card_schema.py

Issue 10：Implement seed prompt generator

目标：基于 state card 生成接续 prompt。

要求：

* CLI 输出；
* 支持复制友好格式；
* 包含 goal/current_state/next_action/avoid_repeating/important_files。

验收：

uv run agentboard seed <id>

Issue 11：Implement Web Dashboard

目标：实现基础 Web UI。

要求：

* FastAPI；
* Jinja；
* Dashboard；
* Workline detail；
* Sessions；
* Worktrees；
* Seed prompt；
* copy buttons。

验收：

uv run agentboard web

Issue 12：Implement redaction and ignore rules

目标：保护敏感信息。

要求：

* .agentboardignore；
* secret regex；
* LLM 前 redaction；
* 测试。

验收：

pytest tests/test_redaction.py

Issue 13：Implement demo mode

目标：clone 后无真实数据也能展示。

要求：

* examples；
* demo DB；
* agentboard demo；
* agentboard web --demo。

验收：

uv run agentboard demo
uv run agentboard web --demo

Issue 14：Implement SSH remote collector

目标：支持远程机器只读扫描。

要求：

* machines.yaml 中配置 ssh machine；
* 使用系统 ssh；
* 执行只读命令；
* timeout；
* failure 不影响本地扫描。

验收：

uv run agentboard scan --machine h200

⸻

18. README 首版结构

README 应包含：

# Agent Session Workboard
## What is this?
## Why?
## Features
## Quickstart
## Demo
## Configuration
## Supported Sources
- Git worktrees
- Codex CLI sessions
- Claude Code transcripts
- tmux sessions
- SSH remote machines
## CLI Commands
## Web UI
## Privacy and Redaction
## Development
## Roadmap

⸻

19. 验收标准

最终 MVP 完成时，必须满足：

1. clone 后能安装；
2. demo mode 能跑；
3. 本地 scan 不崩；
4. 没有 Codex/Claude/tmux 时也能正常运行；
5. 有真实 session 时能建立 session/event；
6. 有 git repo/worktree 时能建立 worktree；
7. 能生成 workline；
8. 能生成 state card；
9. 能生成 seed prompt；
10. Web UI 能展示 Dashboard 和详情页；
11. README 说明清楚；
12. tests 能跑通；
13. 敏感信息进入 LLM 前会被 redaction。

⸻

20. 最小成功体验

用户运行后，第一屏应该让人立刻感觉有用：

You have 12 active work lines.
Most useful to continue:
1. codescout eval debug — next: rerun Lite eval with bf16 vLLM config
2. ascend sglang setup — next: verify CANN image and run Qwen3.5 smoke test
3. codex auth issue — next: inspect installed codex subcommands; do not retry `codex auth status`
Needs review:
1. resume-css — dirty files: github.css, resume.md
Suggested fork:
1. codex-remote-login — old session repeated failed auth flow 4 times; generate clean seed prompt

如果这个体验达到了，就说明 MVP 成功。

⸻

21. 后续路线图

MVP 后可以考虑：

* Claude Code hooks 实时接入；
* Codex session 文件 watcher；
* Cursor / VS Code 插件；
* GitHub issue/PR 关联；
* workline 手动 merge/split；
* state card 手动编辑；
* vector search；
* full-text search；
* richer timeline view；
* session health 趋势；
* 多 agent 对比同一任务；
* 一键生成新的 Codex/Claude 启动命令；
* 自动创建 worktree，但默认仍需用户确认；
* team mode。

⸻

22. 给 DeepSeek / Copilot 的执行指令

请按照本文档实现项目。优先级如下：

1. 先完成 repo skeleton、CLI、config、DB；
2. 再完成 git/codex/claude/tmux collectors；
3. 再完成 workline clustering；
4. 再完成 LLM state card；
5. 再完成 seed prompt；
6. 再完成 Web UI；
7. 最后完成 demo、tests、README、redaction、remote SSH。

开发要求：

* 每个 issue 独立 commit；
* 每完成一个 issue 跑测试；
* 不要一上来引入复杂前端；
* 不要把实现写成一个大文件；
* 不要假设 Codex/Claude JSONL 格式固定；
* 不要因为某个 collector 失败导致整个 scan 失败；
* 保持 README 与实际命令一致；
* 所有命令必须支持 --help；
* 所有外部命令必须有 timeout；
* 所有 LLM 输出必须 schema validation。

最终交付：

uv sync
uv run agentboard demo
uv run agentboard web --demo

应该可以直接启动 demo dashboard。

真实使用：

uv run agentboard init
uv run agentboard scan
uv run agentboard summarize
uv run agentboard web

应该可以展示本机真实工作线。