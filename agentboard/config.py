"""Configuration loading for Agent Session Workboard."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_YAML = """\
# Agent Session Workboard configuration
workspace:
  data_dir: ~/.agentboard

# Machines to discover sessions on. The local machine is required; add SSH
# machines to drive agent sessions running elsewhere (reached purely over SSH).
machines:
  - name: local
    type: local
    codex_home: ~/.codex
    claude_home: ~/.claude
    tmux: true
  # - name: h200
  #   type: ssh
  #   host: h200            # must be reachable via `ssh h200` (use ~/.ssh/config)
  #   codex_home: ~/.codex
  #   claude_home: ~/.claude
  #   tmux: true

# LLM used only for per-session summaries (title + history recap + missed items).
# Optional — everything else works without it.
llm:
  provider: openai_compatible
  model: deepseek-chat
  base_url: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
  reasoning_effort: medium

# Remote access. When enabled the server binds bind_host and requires the token.
remote:
  enabled: false
  bind_host: "0.0.0.0"

auth:
  enabled: true
  bearer_token: ""   # auto-generated and written back on first remote run

voice:
  enabled: false
  tts_provider: browser
  openai_api_key_env: OPENAI_API_KEY
  language: zh-CN

# LLM conversation summaries: better titles + history recap + missed items.
# Master switch; only active when the llm api key above is set. The dashboard
# also has a ✨ toggle you can flip at runtime.
summary:
  enabled: true
  recent_count: 15
"""


class WorkspaceConfig(BaseModel):
    data_dir: str = "~/.agentboard"


class MachineConfig(BaseModel):
    name: str
    type: str = "local"          # "local" | "ssh"
    host: str | None = None      # ssh host (for type == "ssh")
    codex_home: str | None = None
    claude_home: str | None = None
    tmux: bool = True


class LLMConfig(BaseModel):
    provider: str = "openai_compatible"
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    api_key: str | None = None
    reasoning_effort: str = ""


class RemoteConfig(BaseModel):
    enabled: bool = False
    bind_host: str = "0.0.0.0"


class AuthConfig(BaseModel):
    enabled: bool = True
    bearer_token: str = ""


class VoiceConfig(BaseModel):
    enabled: bool = False
    stt_provider: str = "browser"
    tts_provider: str = "browser"
    openai_api_key_env: str = "OPENAI_API_KEY"
    language: str = "zh-CN"


class SummaryConfig(BaseModel):
    """LLM conversation summaries (better titles + history recap + missed items).

    The master on/off; takes effect only when an LLM API key is configured.
    The dashboard exposes a runtime toggle that overrides ``enabled`` and is
    persisted separately so the YAML (and its comments) stay untouched.
    """
    enabled: bool = True
    recent_count: int = 15   # how many recent conversations the batch button covers


class Config(BaseModel):
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    machines: list[MachineConfig] = Field(
        default_factory=lambda: [MachineConfig(name="local", type="local")]
    )
    llm: LLMConfig = Field(default_factory=LLMConfig)
    remote: RemoteConfig = Field(default_factory=RemoteConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path)))


def get_default_config_path() -> Path:
    return _expand("~/.agentboard/config.yaml")


def get_default_data_dir() -> Path:
    return _expand("~/.agentboard")


def load_config(path: str | Path | None = None) -> Config:
    """Load config from YAML, or return defaults if the file is absent."""
    path = Path(path) if path else get_default_config_path()
    if not path.exists():
        return Config()
    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    return Config(**raw)


def init_config(path: str | Path | None = None, force: bool = False) -> Path:
    """Create the config directory and write a default config file."""
    config_path = Path(path).expanduser().resolve() if path else get_default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and not force:
        print(f"Config already exists at {config_path}")
        return config_path
    config_path.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
    print(f"Config created at {config_path}")
    return config_path
