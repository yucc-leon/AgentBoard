# Agent Session Workboard 🧠

*[English](README.md) · [中文](README.zh-CN.md)*

在任何浏览器(包括手机)上,驱动你机器上——以及它经 SSH 连过去的远程机器上——正在跑的 Codex / Claude Code 会话。看 agent 在干什么、打字回它、把一个旧对话从中断的地方接着跟下去。

![仪表盘](docs/screenshots/dashboard.png)

## 是什么

一个管理 AI 编码 agent 会话的小型 web hub。一个会话,就是**一个跑着 agent CLI 的 tmux pane**;AgentBoard 把它们(本地的和 SSH 远程的)连同你过往的 Codex / Claude 对话一起,按项目列出来。打开一个对话,直接接着打字就行。配上 LLM 后,每个对话还会有一个标题和一张**恢复卡片**——当前进展、下一步、可能漏掉的事项——几秒钟就能找回上下文。

![恢复卡片](docs/screenshots/recovery-card.png)

## 怎么运作

- **发现** —— 用 `tmux list-panes`(本地或 `ssh <host> tmux …`)找到在跑的 agent;扫 `~/.codex` / `~/.claude` 最近的 JSONL 日志,把过往对话拉出来。没有数据库,远程机器上也不装任何东西。
- **控制** —— `send-keys` 往 pane 里打字,`capture-pane` 和一条 pty 流把输出显示出来。一旦对外暴露,一把 bearer token 把守所有路由。
- **接续** —— 打开一个对话直接打字:它会自动 resume 进 tmux 并把消息送进去,一步到位。那张 LLM 卡片则替你回顾发生了什么、还有什么没收尾。

## 快速开始

```bash
uv sync
uv run agentboard init        # 生成 ~/.agentboard/config.yaml
uv run agentboard web         # 本地 hub:http://127.0.0.1:8765
```

tmux 里已经在跑的 agent 会自动出现。否则点 **＋ New** 起一个。

## 远程访问

```bash
uv run agentboard web --remote
```

它会对外绑定,并打印出 token、访问 URL,还有一个**能扫的二维码**——手机相机一扫就登录(token 存成 cookie 保留 30 天,每台设备扫一次即可)。之后所有路由都要带 token。`agentboard token` 随时重新打印,`agentboard token --rotate` 换一个新的。端口用什么方式暴露都行——Tailscale、`cloudflared`、SSH 反向隧道。

<img src="docs/screenshots/mobile.png" width="300" alt="手机端仪表盘">

> **延迟:** 同一 WiFi 下很跟手;跨网络(另一个 WiFi、蜂窝)会慢一些,走中继更慢。这只影响控制通道的手感——agent 在主机上干活的速度不受影响。

## 命令行

| 命令 | 作用 |
|---|---|
| `agentboard init` | 创建 `~/.agentboard/config.yaml` |
| `agentboard sessions` | 列出各台机器上的 agent 会话 |
| `agentboard send <machine> <name> <msg…>` | 往一个会话里打字发消息 |
| `agentboard new <machine> <cwd> [--command codex] [--name x]` | 起一个会话 |
| `agentboard kill <machine> <name>` | 关掉一个会话 |
| `agentboard summarize [-m machine] [-n name]` | 生成 LLM 总结卡片 |
| `agentboard token [--rotate]` | 打印访问 token + URL + 二维码(或换一个新的) |
| `agentboard web [--port 8765] [--remote]` | 启动 web hub |

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

llm:                    # 可选 —— 只用于标题和总结
  base_url: https://api.deepseek.com
  model: deepseek-v4-flash
  api_key_env: DEEPSEEK_API_KEY

remote:
  enabled: false        # `web --remote` 会把它打开
  bind_host: "0.0.0.0"
```

配了 LLM 时标题由 LLM 生成;没配则退回用开场白的第一句话。

## 隐私

所有状态都只存在本地 `~/.agentboard/` 下。只有你主动要标题/总结时,对话才会发给 LLM,且密钥(API key、token、私钥)会先脱敏。远程访问默认关闭,开启后由 token 把守。

## 开发

```bash
uv sync --extra dev
uv run --extra dev pytest
uv run --extra dev ruff check
```

## 参与贡献

这本来是个自用的小工具,所以你在真实场景里踩到的坑、觉得别扭的地方,正是最值得反馈的。**欢迎 issue、PR,也欢迎点个 ⭐ star。**

## 致谢

交互式控制部分的设计(tmux 常驻会话、用一个 web hub 在任何地方驱动它们)参考了 [StarAgent](https://github.com/SiriusNEO/StarAgent)。

## 许可

MIT
