# Agent Session Workboard 🧠

Keep your machine on, open a browser **from anywhere**, unlock with a key — and
drive every agent conversation running on it: read what Codex / Claude Code are
doing, type replies, interrupt them, start new ones. Local sessions and remote
ones (reached over SSH) show up in one place.

> A session is just a **tmux pane running an agent CLI**. Everything is built on
> three rock-solid primitives — `list-panes`, `send-keys`, `capture-pane` — run
> locally or over SSH. No database, no daemon on remote machines.

---

## Quickstart

```bash
uv sync
uv run agentboard init        # writes ~/.agentboard/config.yaml
uv run agentboard web         # local hub at http://127.0.0.1:8765
```

Already have agents running in tmux? They appear automatically. Otherwise click
**🚀 New Session** to launch one.

### Reach it from anywhere

```bash
uv run agentboard web --remote
# 🔐 prints a bearer token and an access URL like
#    http://0.0.0.0:8765/?token=ab_xxxxxxxx
```

Then expose the port however you like and open the URL on your phone/laptop:

```bash
tailscale funnel 8765                        # easiest: auto HTTPS
cloudflared tunnel --url http://localhost:8765
ssh -R 80:localhost:8765 serveo.net          # quick & dirty
```

With `--remote`, **every** route requires the token (pages redirect to a login,
`/api` and the WebSocket return 401). The token is generated once and saved back
into your config.

---

## What you can do

The dashboard has two tiers:

- **🟢 Live now** — agents currently running in tmux (local or SSH). Drive them
  directly: read, send messages, interrupt.
- **💬 Conversations** — your full Codex/Claude history from their JSONL logs,
  across every project, with recognizable titles. Read or summarize any of them;
  **Resume** a closed one to bring it back as a live tmux session. A conversation
  that's already live links straight to its operate page.

Other things:

- **See all sessions** across local + SSH machines, agents first, each with a
  one-line summary and a badge for unresolved items.
- **Chat** — read the parsed transcript (rich for local Codex/Claude via their
  JSONL logs; screen-capture fallback for remote) and send messages.
- **Terminal** — a live `capture-pane` stream with Interrupt / Esc / Enter keys.
- **Summarize** — an optional LLM pass over one conversation that produces a
  recognizable title, a history recap, the next action, and **possibly-missed
  items** (TODOs/questions left dangling). Cached and only regenerated when the
  conversation grows.
- **New / Kill** — launch an agent in a fresh tmux session (with a directory
  picker that works over SSH too), or tear one down.

---

## CLI

| Command | What it does |
|---|---|
| `agentboard init` | Create `~/.agentboard/config.yaml` |
| `agentboard sessions` | List agent sessions across machines |
| `agentboard send <machine> <name> <msg…>` | Type a message into a session |
| `agentboard new <machine> <cwd> [--command codex] [--name x]` | Start a session |
| `agentboard kill <machine> <name>` | Kill a session |
| `agentboard summarize [-m machine] [-n name]` | Build LLM summary cards |
| `agentboard web [--port 8765] [--remote]` | Start the web hub |

---

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

llm:                    # optional — only used for summaries
  base_url: https://api.deepseek.com
  model: deepseek-chat
  api_key_env: DEEPSEEK_API_KEY

remote:
  enabled: false        # `web --remote` flips this on
  bind_host: "0.0.0.0"

auth:
  enabled: true
  bearer_token: ""      # auto-generated on first remote run
```

Remote machines need nothing installed — they're driven entirely through
`ssh <host> tmux …`, so SSH key access and a tmux server are the only
requirements.

---

## Privacy

- All state lives locally under `~/.agentboard/`.
- Transcripts are sent to an LLM **only** when you ask for a summary, and secrets
  (API keys, tokens, private keys) are redacted first.
- Remote access is off by default and token-gated when on.

---

## Architecture

```
agentboard/
  core/
    tmux.py         # list-panes / send-keys / capture-pane — local or over SSH
    sessions.py     # discover & address sessions = (machine, tmux name)
    transcript.py   # parse Codex/Claude JSONL → chat turns; screen fallback
  intelligence/
    llm.py          # OpenAI-compatible client
    summary.py      # per-session SessionCard (title/recap/next/open-items) + cache
  auth/middleware.py# default-deny bearer-token auth
  web/app.py        # one control API + one WebSocket; Jinja pages
  cli.py · config.py · voice/
```

## Development

```bash
uv sync --extra dev
uv run --extra dev pytest      # core, transcript, summary, auth, web
uv run --extra dev ruff check
```

## License

MIT
