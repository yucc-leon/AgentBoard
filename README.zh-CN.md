# Agent Session Workboard 🧠

*[English](README.md) · [中文](README.zh-CN.md)*

让你的机器开着,**在任何地方**打开浏览器、用一把密钥解锁,就能驱动它上面跑着的每一个 agent 对话:看 Codex / Claude Code 正在做什么、打字回复、打断它们、开新的。本地会话和远程(通过 SSH)会话都汇总在一处。

> 一个会话就是**一个跑着 agent CLI 的 tmux pane**。一切都建立在三个稳固的原语之上——`list-panes`、`send-keys`、`capture-pane`——本地或经 SSH 执行。没有数据库,远程机器上也没有常驻守护进程。

---

## 快速开始

```bash
uv sync
uv run agentboard init        # 写入 ~/.agentboard/config.yaml
uv run agentboard web         # 本地 hub:http://127.0.0.1:8765
```

tmux 里已经有 agent 在跑?它们会自动出现。否则用 **＋ New**(在任意项目分组里,或 **＋ New project…**)启动一个。

### 从任何地方访问

```bash
uv run agentboard web --remote
# 🔐 会打印一个 bearer token 和访问 URL,形如
#    http://0.0.0.0:8765/?token=ab_xxxxxxxx
```

然后用你喜欢的方式把端口暴露出去,在手机/笔记本上打开该 URL:

```bash
tailscale funnel 8765                        # 最省事:自动 HTTPS
cloudflared tunnel --url http://localhost:8765
ssh -R 80:localhost:8765 serveo.net          # 简单粗暴
```

加上 `--remote` 后,**每一个**路由都需要 token(页面会重定向到登录,`/api` 和 WebSocket 返回 401)。token 只生成一次,并写回你的配置文件。

> 💡 国内场景:如果本机开着 Clash 这类全局代理(TUN 模式),Tailscale 的直连打洞可能被劫持而退回中继(延迟偏高)。这只影响控制通道的手感,不影响 agent 在机器上实际执行的速度。

---

## 你能做什么

仪表盘分两层:

- **🟢 实时(Live now)** —— 当前正跑在 tmux 里的 agent(本地或 SSH)。可直接驱动:阅读、发消息、打断。
- **💬 对话(Conversations)** —— 你完整的 Codex/Claude 历史(来自它们的 JSONL 日志),跨所有项目,带 LLM 自动生成的标题(自动、带缓存)。**直接打字即可接着聊**——发送一条消息就会把一个已关闭的对话恢复成实时 tmux 会话并把消息送达;无需单独点 "Resume"。已经在跑的对话则直接链到它的操作页。

其它:

- **查看所有会话** —— 跨本地 + SSH 机器,agent 优先,每条带一行摘要和未决事项的角标。
- **聊天(Chat)** —— 阅读解析后的对话(本地 Codex/Claude 经 JSONL 日志,内容丰富;远程则回退到屏幕截取),并发送消息。
- **终端(Terminal)** —— 真正可交互的终端(xterm.js 接 pty / tmux attach),带移动端按键行(方向键 / Tab / Enter / Esc / Ctrl-C)。
- **总结(Summarize)** —— 可选的 LLM 通道,对一个对话生成:可辨识的标题、历史回顾、下一步动作,以及**可能遗漏的事项**(悬而未决的 TODO/问题)。带缓存,仅当对话增长时才重新生成。
- **新建 / 关闭(New / Kill)** —— 在全新的 tmux 会话里启动一个 agent(目录选择器在 SSH 上也能用),或把它关掉。

---

## 命令行

| 命令 | 作用 |
|---|---|
| `agentboard init` | 创建 `~/.agentboard/config.yaml` |
| `agentboard sessions` | 列出各机器上的 agent 会话 |
| `agentboard send <machine> <name> <msg…>` | 向一个会话打字发消息 |
| `agentboard new <machine> <cwd> [--command codex] [--name x]` | 启动一个会话 |
| `agentboard kill <machine> <name>` | 关闭一个会话 |
| `agentboard summarize [-m machine] [-n name]` | 生成 LLM 总结卡片 |
| `agentboard web [--port 8765] [--remote]` | 启动 web hub |

---

## 配置

`~/.agentboard/config.yaml`:

```yaml
workspace:
  data_dir: ~/.agentboard

machines:
  - name: local
    type: local
    codex_home: ~/.codex
    claude_home: ~/.claude
    tmux: true
  - name: h200
    type: ssh
    host: h200          # 必须能直接 `ssh h200`(用 ~/.ssh/config)
    codex_home: ~/.codex
    claude_home: ~/.claude
    tmux: true

llm:                    # 可选 —— 仅用于标题和总结
  base_url: https://api.deepseek.com
  model: deepseek-v4-flash
  api_key_env: DEEPSEEK_API_KEY

remote:
  enabled: false        # `web --remote` 会把它打开
  bind_host: "0.0.0.0"

auth:
  enabled: true
  bearer_token: ""      # 首次 remote 运行时自动生成
```

远程机器无需安装任何东西——它们完全通过 `ssh <host> tmux …` 驱动,所以只需要 SSH 密钥访问和一个 tmux server。

---

## 隐私

- 所有状态都存在本地 `~/.agentboard/` 下。
- 仅当你主动请求总结时,对话才会发送给 LLM;且密钥(API key、token、私钥)会先被脱敏。
- 远程访问默认关闭,开启时受 token 保护。

---

## 架构

```
agentboard/
  core/
    tmux.py         # list-panes / send-keys / capture-pane —— 本地或经 SSH
    sessions.py     # 发现并寻址会话 = (machine, tmux name)
    transcript.py   # 解析 Codex/Claude JSONL → 聊天回合;屏幕回退
  intelligence/
    llm.py          # OpenAI 兼容客户端
    summary.py      # 每会话的 SessionCard(标题/回顾/下一步/遗漏项)+ 缓存
  auth/middleware.py# 默认拒绝的 bearer-token 鉴权
  web/app.py        # 一个控制 API + 一个 WebSocket;Jinja 页面
  cli.py · config.py · voice/
```

## 开发

```bash
uv sync --extra dev
uv run --extra dev pytest      # core、transcript、summary、auth、web
uv run --extra dev ruff check
```

## 许可

MIT
