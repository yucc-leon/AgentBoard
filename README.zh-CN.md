# AgentBoard 🧠

*[English](README.md) · [中文](README.zh-CN.md)*

我习惯让 Codex 和 Claude Code 在我的 Mac 上跑着,然后人就走开了。AgentBoard 就是我在外面用手机回来瞄一眼的方式:看看 agent 在干嘛、回它两句,或者把上周跑的某个对话翻出来接着弄。基本就这点事。

![仪表盘](docs/screenshots/dashboard-zh.png)

## 是什么

一个套在 agent 会话前面的小网页。一个会话,说白了就是一个跑着 agent CLI 的 tmux pane——所以 AgentBoard 把你开着的那些(本机的,或者经 SSH 连过去的远程机器上的),和硬盘上已经存着的 Codex / Claude 对话,一起列出来。随便点开一个,直接打字就行。

要是你配了 LLM 的 key,它还会给每个对话起个短标题、写一小段:进展到哪了、下一步该干嘛、还有哪些你大概率忘了的尾巴。省得你再从头爬一墙文字,去想自己当时到底在搞什么。

![恢复卡片](docs/screenshots/recovery-card-zh.png)

## 怎么运作

就靠三条 tmux 命令,没别的花样:

- 用 `tmux list-panes`(或 `ssh <host> tmux list-panes`)找到在跑的 agent;过往对话呢,直接读 Codex / Claude 本来就写在 `~/.codex`、`~/.claude` 下的 JSONL 日志。没有数据库,远程机器上也不用装任何东西。
- 用 `send-keys` 往 pane 里打字,用 `capture-pane` 把输出显示出来——终端那个标签页还接了一条真正的 pty 流。
- 你打开一个旧对话、点发送,它就把那个对话在 tmux 里重新拉起来、把消息送进去。不用先点一下 "resume"。

一旦对外暴露,一把 token 看住所有入口。

## 快速开始

```bash
uv sync
uv run agentboard init        # 生成 ~/.agentboard/config.yaml
uv run agentboard web         # 本地 hub:http://127.0.0.1:8765
```

tmux 里已经在跑的 agent 会自己冒出来。想新起一个,点 **＋ New**。

## 用手机连上

```bash
uv run agentboard web --remote
```

它会对外绑定,然后打印一个 token、访问 URL,还有一个二维码。手机一扫就进去了——token 会以 cookie 形式留 30 天,所以每台设备扫一次就行。token 丢了?`agentboard token` 再打印一遍,`agentboard token --rotate` 换个新的。端口怎么暴露随你——Tailscale、`cloudflared`、SSH 反向隧道,都行。

<img src="docs/screenshots/mobile-zh.png" width="300" alt="手机端仪表盘">

速度上提一句:同一个 WiFi 下很跟手;换个网络会慢些;要是流量还走了中继,那就更慢。但慢的只是操作那一下——agent 在你机器上干活,该多快还是多快。

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

不配 LLM key 的话,标题就是你开场白的第一行;配上之后才会好好生成。

## 隐私

所有东西都留在你自己机器上的 `~/.agentboard/` 下。只有你主动要标题或总结时,对话才会发给 LLM,而且密钥(API key、token、私钥)会先被剔掉。远程访问默认不开,开了之后由 token 把守。

## 开发

```bash
uv sync --extra dev
uv run --extra dev pytest
uv run --extra dev ruff check
```

## 参与贡献

这本来就是个自用的小工具,所以你在真实使用里踩到的坑,特别值得反馈。欢迎提 issue、PR,也欢迎点个 star。

## 致谢

可交互的那部分——tmux 常驻会话、用一个 web hub 远程驱动——借鉴了 [StarAgent](https://github.com/SiriusNEO/StarAgent)。

## 许可

MIT
