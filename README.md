# Agent Session Workboard 🧠

*[English](README.md) · [中文](README.zh-CN.md)*

Drive the Codex / Claude Code sessions running on your machine — and its SSH
remotes — from any browser, including your phone. Read what an agent is doing,
type a reply, and pick up an old conversation where it left off.

![Dashboard](docs/screenshots/dashboard.png)

## What it is

A small web hub for your AI coding-agent sessions. A session is a **tmux pane
running an agent CLI**; AgentBoard lists every one of them (local and over SSH)
alongside your past Codex / Claude conversations, grouped by project. Open a
conversation and just keep typing. An optional LLM pass gives each one a title
plus a **recovery card** — current state, next step, and possibly-missed items —
so you get back into context in seconds.

![Recovery card](docs/screenshots/recovery-card.png)

## How it works

- **Discover** — `tmux list-panes` (locally or `ssh <host> tmux …`) finds running
  agents; recent `~/.codex` / `~/.claude` JSONL logs surface past conversations.
  No database, and nothing is installed on remote machines.
- **Control** — `send-keys` types into a pane; `capture-pane` and a pty stream
  show output. One bearer token guards every route once the hub is exposed.
- **Continue** — open a conversation and type: it resumes into tmux and delivers
  your message in one step. The LLM card recaps what happened and what's still open.

## Quickstart

```bash
uv sync
uv run agentboard init        # writes ~/.agentboard/config.yaml
uv run agentboard web         # local hub at http://127.0.0.1:8765
```

Agents already running in tmux appear automatically. Otherwise use **＋ New** to
launch one.

## Remote access

```bash
uv run agentboard web --remote
```

This binds publicly and prints the token, the access URLs, and a **scannable QR
code** — point your phone's camera at it to log in (the token is saved as a
cookie for 30 days, so you scan once per device). Every route then requires the
token. `agentboard token` reprints it anytime; `agentboard token --rotate` issues
a new one. Expose the port with whatever you like — Tailscale, `cloudflared`, an
SSH reverse tunnel.

<img src="docs/screenshots/mobile.png" width="300" alt="Mobile dashboard">

> **Latency:** snappy on the same Wi-Fi; slower across networks (another Wi-Fi,
> cellular) and slower still through a relay. This only affects the control
> channel — the agent's own work on the host runs at full speed.

## CLI

| Command | What it does |
|---|---|
| `agentboard init` | Create `~/.agentboard/config.yaml` |
| `agentboard sessions` | List agent sessions across machines |
| `agentboard send <machine> <name> <msg…>` | Type a message into a session |
| `agentboard new <machine> <cwd> [--command codex] [--name x]` | Start a session |
| `agentboard kill <machine> <name>` | Kill a session |
| `agentboard summarize [-m machine] [-n name]` | Build LLM summary cards |
| `agentboard token [--rotate]` | Print the access token + URLs + QR (or rotate it) |
| `agentboard web [--port 8765] [--remote]` | Start the web hub |

## Configuration

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
    host: h200          # must work as `ssh h200` (use ~/.ssh/config)
    codex_home: ~/.codex
    claude_home: ~/.claude
    tmux: true

llm:                    # optional — only used for titles & summaries
  base_url: https://api.deepseek.com
  model: deepseek-v4-flash
  api_key_env: DEEPSEEK_API_KEY

remote:
  enabled: false        # `web --remote` flips this on
  bind_host: "0.0.0.0"
```

Titles are LLM-generated when an LLM is configured; otherwise they fall back to
the first line of your opening message.

## Privacy

State lives locally under `~/.agentboard/`. Transcripts are sent to an LLM only
when you ask for a title/summary, with secrets (API keys, tokens, private keys)
redacted first. Remote access is off by default and token-gated when on.

## Development

```bash
uv sync --extra dev
uv run --extra dev pytest
uv run --extra dev ruff check
```

## Contributing

This started as a personal tool, so the rough edges you hit in real use are
exactly what's most useful to hear about. **Issues, PRs, and ⭐ stars are all
welcome.**

## Acknowledgements

The interactive control design (tmux-first sessions, a web hub to drive them from
anywhere) was informed by [StarAgent](https://github.com/SiriusNEO/StarAgent).

## License

MIT
