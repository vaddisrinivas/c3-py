"""c3-py — WhatsApp AI apps via MCP."""

from __future__ import annotations
import asyncio
import contextlib
import json
import os
import re
import sys
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import anyio
import dataset as _dataset
import typer
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import (
    BlobResourceContents,
    JSONRPCMessage,
    JSONRPCNotification,
    Resource,
    ResourceTemplate,
    TextContent,
    TextResourceContents,
    Tool,
)
from pydantic import BaseModel
from watchfiles import awatch

# ── Roles ──
ROLE_HOSTS = "hosts"
ROLE_ADMINS = "admins"
ROLE_PARTICIPANTS = "session_participants"
ROLE_GROUP = "session_group"
ROLE_ELEVATED = "elevated_participants"

# ── Event types ──
EVT_MESSAGE = "message"
EVT_SESSION_STOP = "session_stop"
EVT_SETUP_START = "setup_start"
EVT_PHASE_EXPIRED = "phase_expired"
EVT_POLL_UPDATE = "poll_update"
EVT_SKILL_LOAD = "skill_load"
EVT_SKILL_UNLOAD = "skill_unload"
EVT_SESSION_START = "session_start"
EVT_CRON_TICK = "cron_tick"
EVT_TOOLS_CHANGED = "_tools_changed"

# ── Log tags ──
LOG_POLICY = "policy"
LOG_BAILEYS = "baileys"
LOG_ENGINE = "engine"
LOG_APP = "app"
LOG_C3 = "c3"
LOG_TOOL = "tool"
LOG_CRON = "cron"
LOG_MEDIA = "media"
LOG_ERROR = "error"

# ── Files ──
FILE_APP_JSON = "app.json"
FILE_AGENT_JSON = "agent.json"
FILE_CLAUDE_MD = "CLAUDE.md"
FILE_MCP_JSON = ".mcp.json"
FILE_CONFIG_JSON = "config.json"
FILE_MEMORY_DB = "memory.db"
DIR_SKILLS = "skills"
DIR_SESSIONS = "sessions"
DIR_APPS = "apps"
DIR_LOGS = "logs"

# ── Tokens ──
TOKEN_HOST = "host"
TOKEN_GROUP = "group"

# ── Trust levels ──
TRUST_BUILTIN = "builtin"
TRUST_COMMUNITY = "community"

# ── Protected files that save_file cannot overwrite ──
PROTECTED_FILES = {FILE_CONFIG_JSON, FILE_APP_JSON, FILE_AGENT_JSON, FILE_MCP_JSON, FILE_MEMORY_DB}

# ── Allowed entity keys for memory_write ──
ENTITY_KEYS = {
    "app",
    "entity",
    "name",
    "value",
    "data",
    "score",
    "tags",
    "metadata",
    "notes",
    "status",
}


class BaileysError(Exception):
    pass


class BaileysDisconnectedError(BaileysError):
    pass


class BaileysTimeoutError(BaileysError):
    pass


from typing import Literal as Lit

MediaType = Lit[
    "image", "video", "audio", "voice_note", "sticker", "document", "live_location", None
]
TrustLevel = Lit["builtin", "verified", "community", "untrusted"]


class Message(BaseModel):
    jid: str
    sender: str
    push_name: str
    text: str
    timestamp: int
    is_group: bool
    message_id: str | None = None
    media_path: str | None = None
    media_type: MediaType = None
    media_mimetype: str | None = None
    media_size: int | None = None
    media_duration: int | None = None
    media_filename: str | None = None
    catchup: bool = False


class GroupMember(BaseModel):
    jid: str
    name: str
    is_admin: bool
    lid: str | None = None


class HostConfig(BaseModel):
    jid: str
    name: str
    lid: str | None = None


_PKG = Path(__file__).parent
import yaml as _yaml

_C = _yaml.safe_load((_PKG / "c3.yaml").read_text())
_MSG = _C["messages"]


def _env(key, default):
    """Read C3_KEY env var, coerce to type of default."""
    v = os.environ.get(f"C3_{key.upper()}")
    if v is None:
        return default
    if isinstance(default, bool):
        return v.lower() in ("1", "true", "yes")
    try:
        return type(default)(v)
    except (ValueError, TypeError):
        return default


def _build_app_config():
    fields = {name: _env(name, _C[path[0]][path[1]]) for name, path in _C["config_fields"].items()}
    fields["hosts"] = []
    fields[ROLE_ADMINS] = []
    annot = {k: type(v) for k, v in fields.items()}
    annot["hosts"] = list[HostConfig]
    annot[ROLE_ADMINS] = list[HostConfig]
    return type("AppConfig", (BaseModel,), {"__annotations__": annot, **fields})


AppConfig = _build_app_config()
_cfg = AppConfig()
_MIME_TYPES: dict[str, str] = _C["mime_types"]


class AccessPolicy(BaseModel):
    commands: dict[str, list[str]] = {}
    dm: list[str] = []
    group: list[str] = []


class AppManifest(BaseModel):
    name: str
    access: AccessPolicy
    trust_level: TrustLevel = TRUST_BUILTIN
    sandboxed: bool = False
    allowed_tools: list[str] = []
    allowed_resources: list[str] = []
    description: str = ""
    memory_schema: dict[str, Any] = {}
    crons: list[dict[str, str]] = []


class ToolDef(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class AppMCPProxy:
    def __init__(self, name: str, params: dict, agent_dir: Path):
        self.name = name
        self._params = params
        self._agent_dir = agent_dir
        self._session: Any | None = None
        self.tools: list[Tool] = []
        self._ready = asyncio.Event()

    @property
    def tool_names(self) -> set[str]:
        return {t.name for t in self.tools}

    async def run(self) -> None:
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        env = {
            **os.environ,
            **{
                k: v.replace("${agent_dir}", str(self._agent_dir))
                for k, v in self._params.get("env", {}).items()
            },
        }
        try:
            async with stdio_client(
                StdioServerParameters(
                    command=self._params["command"], args=self._params.get("args", []), env=env
                )
            ) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    self.tools = (await s.list_tools()).tools
                    self._session = s
                    self._ready.set()
                    log("aggregator", f"{self.name}: {len(self.tools)} tools loaded")
                    await anyio.sleep_forever()
        except Exception as e:
            log("aggregator", f"{self.name} error: {e}")
        finally:
            self._session = None

    async def call_tool(self, name: str, arguments: dict) -> list[TextContent]:
        await asyncio.wait_for(self._ready.wait(), timeout=_cfg.app_init_timeout)
        if not self._session:
            return _R(f"Error: MCP server '{self.name}' not connected")
        return (await self._session.call_tool(name, arguments)).content


class ChatAdapter(ABC):
    admin_jid: str = ""
    on_message: Callable[[Message], Awaitable[None]] | None = None
    on_ready: Callable[[], Awaitable[None]] | None = None
    on_poll_update: Callable[[str, dict], Awaitable[None]] | None = None

    @abstractmethod
    async def connect(self) -> None: ...
    @abstractmethod
    async def send(self, jid: str, text: str) -> None: ...
    @abstractmethod
    async def send_poll(self, jid: str, question: str, options: list[str]) -> str: ...
    @abstractmethod
    async def resolve_group(self, invite_link: str) -> str: ...
    @abstractmethod
    async def get_group_members(self, group_jid: str) -> list[GroupMember]: ...
    async def react(self, jid: str, message_id: str, emoji: str) -> None:
        raise NotImplementedError

    async def send_presence(self, jid: str, presence: str = "composing") -> None:
        raise NotImplementedError

    async def send_image(self, jid: str, path: str, caption: str = "") -> None:
        raise NotImplementedError

    async def send_video(self, jid: str, path: str, caption: str = "") -> None:
        raise NotImplementedError

    async def send_audio(self, jid: str, path: str, ptt: bool = False) -> None:
        raise NotImplementedError

    async def send_document(
        self, jid: str, path: str, filename: str = "", mimetype: str = ""
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_name(self, jid: str) -> str: ...
    def is_group_id(self, id: str) -> bool:
        return False

    def is_valid_invite(self, link: str) -> bool:
        return False

    def extract_name(self, id: str) -> str:
        return id


WAAdapter = ChatAdapter  # compat
WAMessage = Message  # compat
_log_file: Path | None = None


def setup_logging(d):
    global _log_file
    p = Path(d) / DIR_LOGS
    p.mkdir(parents=True, exist_ok=True)
    _log_file = p / f"c3-{datetime.now().strftime('%Y-%m-%d')}.log"


def log(tag, msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] [{tag}] {msg}"
    print(line, file=sys.stderr, flush=True)
    if _log_file:
        with contextlib.suppress(Exception):
            with open(_log_file, "a") as f:
                f.write(line + "\n")


def parse_duration(value: Any, fallback: int | None = None) -> int:
    if fallback is None:
        fallback = _cfg.default_duration
    if value is None or value == "":
        return fallback
    if isinstance(value, (int, float)):
        return int(value)
    m = re.match(r"^(\d+)(s|m)?$", str(value).strip(), re.I)
    if not m:
        return int(str(value)) if str(value).isdigit() else fallback
    n = int(m.group(1))
    return n * 60 if (m.group(2) or "").lower() == "m" else n


def pick(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _load_manifest(dir_path: Path) -> dict:
    for f in (FILE_APP_JSON, FILE_AGENT_JSON):
        d = _read_json(dir_path / f)
        if d:
            return d
    return {}


def _scan_dirs(base: Path, bundled: Path):
    seen: set[str] = set()
    for root in [base, bundled]:
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and d.name not in seen:
                seen.add(d.name)
                yield d.name, d


class AccessControl:
    def __init__(self, manifest: AppManifest, config: AppConfig):
        self._manifest = manifest
        self._jid_to_token: dict[str, str] = {}
        self._token_to_jid: dict[str, str] = {}
        self._static: dict[str, set[str]] = {}
        self._dynamic: dict[str, set[str]] = {}
        self._mask_pairs: list[tuple[str, str]] | None = None
        self._static["hosts"] = self._register_entries(config.hosts, lambda _: TOKEN_HOST)
        self._static[ROLE_ADMINS] = self._register_entries(config.admins, lambda _: TOKEN_HOST)
        log(LOG_POLICY, f"loaded: {manifest.name}")

    def register(self, jid: str, token: str) -> None:
        if not jid or not token:
            return
        # Bug #1: disambiguate duplicate tokens
        orig = token
        i = 2
        while token in self._token_to_jid and self._token_to_jid[token] != jid:
            token = f"{orig}_{i}"
            i += 1
        self._jid_to_token[jid] = token
        self._mask_pairs = None  # invalidate cache
        if token not in self._token_to_jid:
            self._token_to_jid[token] = jid

    def mask(self, text: str) -> str:
        if self._mask_pairs is None:
            self._mask_pairs = sorted(
                self._jid_to_token.items(), key=lambda x: len(x[0]), reverse=True
            )
        for jid, token in self._mask_pairs:
            text = text.replace(jid, token)
        return text

    def unmask(self, token: str) -> str:
        return self._token_to_jid.get(token, token)

    def mask_meta(self, meta: dict) -> dict:
        return {k: self.mask(str(v)) if isinstance(v, str) else v for k, v in meta.items()}

    @staticmethod
    def _normalize_jid(jid: str) -> str:
        """Strip device suffix: 123:5@s.whatsapp.net → 123@s.whatsapp.net"""
        if ":" in jid and "@" in jid:
            phone, domain = jid.split("@", 1)
            return phone.split(":")[0] + "@" + domain
        return jid

    def has_role(self, jid: str, role: str) -> bool:
        s = self._static.get(role, set()) | self._dynamic.get(role, set())
        return jid in s or self._normalize_jid(jid) in s

    def is_host(self, jid: str) -> bool:
        return self.has_role(jid, ROLE_HOSTS)

    def is_participant(self, jid: str) -> bool:
        return jid in self._dynamic.get(ROLE_PARTICIPANTS, set())

    def is_elevated(self, jid: str) -> bool:
        return jid in self._dynamic.get(ROLE_ELEVATED, set())

    def is_known(self, jid: str) -> bool:
        return jid in self._jid_to_token

    def can_reach(self, jid: str, is_group: bool, text: str) -> bool:
        a = self._manifest.access
        if not is_group:
            if text.strip().startswith("/"):
                return any(
                    self.has_role(jid, r)
                    for r in a.commands.get(text.strip().split()[0].lower(), [])
                )
            return any(self.has_role(jid, r) for r in a.dm)
        return any(self.has_role(jid, r) for r in a.group)

    def grant(self, role: str, entries: list[dict]) -> None:
        for e in entries:
            self.register(e["jid"], e["token"])
        self._dynamic.setdefault(role, set()).update(e["jid"] for e in entries)
        log(LOG_POLICY, f"grant({role}): {len(entries)}")

    def grant_jid(self, role: str, jid: str) -> None:
        self._dynamic.setdefault(role, set()).add(jid)

    def revoke(self, role: str) -> None:
        self._dynamic.pop(role, None)
        log(LOG_POLICY, f"revoke({role})")

    def revoke_all_session(self) -> None:
        for role in [ROLE_PARTICIPANTS, ROLE_GROUP, ROLE_ELEVATED]:
            self._dynamic.pop(role, None)

    def _register_entries(self, entries: list[HostConfig], tok_fn) -> set[str]:
        jids: set[str] = set()
        for x in entries:
            jids.add(x.jid)
            tok = tok_fn(x)
            self.register(x.jid, tok)
            if x.lid:
                jids.add(x.lid)
                self._jid_to_token[x.lid] = tok
                self._mask_pairs = None
        return jids


class SessionEngine:
    def __init__(
        self, wa: ChatAdapter, notify: Callable, ctrl: AccessControl, agent_dir: str | Path = "."
    ):
        self._wa = wa
        self._notify = notify
        self._sessions_dir = Path(agent_dir) / DIR_SESSIONS
        self._ctrl = ctrl
        self._phase_timers: dict[str, asyncio.TimerHandle] = {}
        self._poll_listeners: dict[str, Callable] = {}
        self._poll_tallies: dict[str, dict] = {}
        self._stop_poll_map: dict[str, str] = {}
        self._active_sessions: dict[str, str] = {}
        self._loaded_apps: set[str] = set()
        self.on_catchup: Callable[[], Awaitable[None]] | None = None
        orig = wa.on_poll_update

        async def _poll_handler(poll_id: str, tally: dict) -> None:
            await self._handle_stop_poll(poll_id, tally)
            await self._dispatch_poll(poll_id, tally)
            if orig:
                await orig(poll_id, tally)

        wa.on_poll_update = _poll_handler

    def resolve_group(self, token: str | None) -> str | None:
        if token and token != "group":
            return self._ctrl.unmask(token)
        sessions = list(self._active_sessions.keys())
        return sessions[0] if sessions else None

    def set_active(self, group_jid: str, name: str) -> None:
        if self._active_sessions and group_jid not in self._active_sessions:
            self.clear_active()
        self._active_sessions[group_jid] = name
        log(LOG_ENGINE, f"active: {name} (1 session)")

    def clear_active(self, group_jid: str | None = None) -> None:
        if group_jid:
            self._active_sessions.pop(group_jid, None)
        else:
            self._active_sessions.clear()
        log(LOG_ENGINE, f"cleared (remaining: {len(self._active_sessions)})")

    def track_poll(self, poll_id: str, group_jid: str, question: str) -> None:
        self._poll_tallies[poll_id] = {}

        async def listener(pid: str, tally: dict) -> None:
            if pid != poll_id:
                return
            self._poll_tallies[poll_id] = tally
            await self._notify(
                f'POLL UPDATE — "{question}"\n'
                + "\n".join(f"  {o}: {len(v)} votes" for o, v in tally.items()),
                {"type": EVT_POLL_UPDATE, "poll_id": poll_id, "group_jid": group_jid},
            )

        self._poll_listeners[poll_id] = listener

    def get_poll_tally(self, poll_id: str) -> dict:
        return self._poll_tallies.get(poll_id, {})

    async def _dispatch_poll(self, poll_id: str, tally: dict) -> None:
        for listener in list(self._poll_listeners.values()):
            try:
                await listener(poll_id, tally)
            except Exception as e:
                log(LOG_ENGINE, f"poll listener error: {e}")

    def set_phase_timer(self, group_jid: str, seconds: int, phase_name: str) -> None:
        self._cancel_timer(group_jid)
        loop = asyncio.get_running_loop()

        async def _fire() -> None:
            await asyncio.sleep(_cfg.phase_expiry_grace)
            tally_lines: list[str] = []
            for pid, tally in list(self._poll_tallies.items()):
                for opt, voters in tally.items():
                    tally_lines.append(f"  {opt}: {len(voters)} — {', '.join(voters)}")
                self._poll_tallies.pop(pid, None)
                self._poll_listeners.pop(pid, None)
            tally_str = (
                _MSG["phase_votes_header"] + "\n".join(tally_lines)
                if tally_lines
                else _MSG["phase_no_votes"]
            )
            await self._notify(
                _MSG[EVT_PHASE_EXPIRED].format(phase=phase_name) + tally_str,
                {"type": EVT_PHASE_EXPIRED, "group_jid": group_jid, "phase": phase_name},
            )

        self._phase_timers[group_jid] = loop.call_later(
            seconds, lambda: asyncio.ensure_future(_fire())
        )
        log(LOG_ENGINE, f"timer: {phase_name} ({seconds}s)")

    def clear_all_timers(self) -> None:
        for handle in self._phase_timers.values():
            handle.cancel()
        self._phase_timers.clear()
        self._poll_tallies.clear()
        # Clean up poll listeners for phase polls (not stop polls)
        stop_poll_ids = set(self._stop_poll_map.values())
        self._poll_listeners = {k: v for k, v in self._poll_listeners.items() if k in stop_poll_ids}

    def _cancel_timer(self, group_jid: str) -> None:
        h = self._phase_timers.pop(group_jid, None)
        if h:
            h.cancel()

    def _app_dirs(self) -> list[Path]:
        base = self._sessions_dir.parent
        bundled = _PKG / DIR_APPS
        return [d / DIR_SKILLS for _, d in _scan_dirs(base, bundled) if (d / DIR_SKILLS).is_dir()]

    def _list_apps(self) -> list[str]:
        return sorted({f.stem for d in self._app_dirs() for f in d.glob("*.md")})

    def _load_app_content(self, name: str) -> str | None:
        return next(
            (p.read_text() for d in self._app_dirs() for p in [d / f"{name}.md"] if p.exists()),
            None,
        )

    async def handle(self, msg: Message) -> bool:
        if msg.is_group or not msg.text.startswith("/"):
            return False
        parts = msg.text.strip().split()
        cmd = parts[0].lower()
        handler = (
            self._cmd_app
            if cmd in ("/app", "/agent")
            else getattr(self, f"_cmd{cmd.replace('/', '_')}", None)
        )
        return await handler(msg, parts) if handler else False

    async def _cmd_start(self, msg, parts) -> bool:
        # Bug #16: clear old stop poll entry before creating new one
        self._stop_poll_map.pop(msg.sender, None)
        self._stop_poll_map[msg.sender] = await self._wa.send_poll(
            msg.sender, _MSG["stop_poll_question"], _MSG["stop_poll_options"]
        )
        args = " ".join(parts[1:]) if len(parts) > 1 else ""
        await self._notify(
            _MSG["start_notify"].format(args=args or "(none)"),
            {"type": EVT_SETUP_START, "host_jid": TOKEN_HOST, **({"args": args} if args else {})},
        )
        return True

    async def _cmd_stop(self, msg, parts) -> bool:
        self.clear_all_timers()
        self.clear_active()
        await self._notify(_MSG["stop_notify"], {"type": EVT_SESSION_STOP, "host_jid": TOKEN_HOST})
        await self._wa.send(msg.sender, _MSG["stop_confirm"])
        return True

    async def _cmd_catchup(self, msg, parts) -> bool:
        if self.on_catchup:
            await self.on_catchup()
        return True

    async def _cmd_clear(self, msg, parts) -> bool:
        self.clear_all_timers()
        self.clear_active()
        await self._wa.send(msg.sender, _MSG["clear_confirm"])
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        (self._sessions_dir / "clear_session").write_text("1")
        return True

    async def _cmd_status(self, msg, parts) -> bool:
        await self._wa.send(
            msg.sender,
            _MSG["status_format"].format(
                sessions=len(self._active_sessions), timers=len(self._phase_timers)
            ),
        )
        return True

    async def _cmd_app(self, msg, parts) -> bool:
        sub = parts[1].lower() if len(parts) > 1 else "list"
        name = parts[2].lower().removesuffix(".md") if len(parts) > 2 else ""
        if sub == "list":
            await self._wa.send(
                msg.sender,
                _MSG["app_list_format"].format(
                    available=", ".join(self._list_apps()) or "none",
                    loaded=", ".join(sorted(self._loaded_apps)) or "none",
                ),
            )
            return True
        if sub == "add" and name:
            content = self._load_app_content(name)
            if not content:
                await self._wa.send(msg.sender, _MSG["app_not_found"].format(name=name))
                return True
            self._loaded_apps.add(name)
            await self._notify(content, {"type": EVT_SKILL_LOAD, "skill": name})
            await self._wa.send(msg.sender, _MSG["app_loaded"].format(name=name))
            return True
        if sub == "remove" and name:
            self._loaded_apps.discard(name)
            await self._notify(
                _MSG["app_unload_notify"].format(name=name),
                {"type": EVT_SKILL_UNLOAD, "skill": name},
            )
            await self._wa.send(msg.sender, _MSG["app_unloaded"].format(name=name))
            return True
        await self._wa.send(msg.sender, _MSG["app_usage"])
        return True

    async def _handle_stop_poll(self, poll_id: str, tally: dict) -> None:
        for host_jid, stop_id in list(self._stop_poll_map.items()):
            if poll_id != stop_id:
                continue
            for _opt, voters in tally.items():
                phone = host_jid.split(":")[0] if ":" in host_jid else host_jid
                norm = AccessControl._normalize_jid(host_jid)
                if not any(v in voters for v in {host_jid, phone, norm}):
                    continue
                del self._stop_poll_map[host_jid]
                self.clear_all_timers()
                self.clear_active()
                await self._notify(
                    _MSG["stop_poll_notify"], {"type": EVT_SESSION_STOP, "host_jid": TOKEN_HOST}
                )
                await self._wa.send(host_jid, "✅ Stop signal sent.")
                return


def _R(t):
    return [TextContent(type="text", text=t)]


BASE_TOOLS: list[ToolDef] = [
    ToolDef(
        name=t["name"],
        description=t["description"],
        input_schema={
            "type": "object",
            "properties": t["properties"],
            **({"required": t["required"]} if "required" in t else {}),
        },
    )
    for t in _C["tools"]
]


def find_app_content(name: str, base: Path) -> list[tuple[str, str]]:
    def _r(p):
        try:
            return p.read_text()
        except OSError:
            return None

    for root in [base, _PKG / DIR_APPS]:
        p = root / name
        if p.is_dir():
            out = [(f"{name}/CLAUDE.md", c) for c in [_r(p / FILE_CLAUDE_MD)] if c]
            out += [
                (f.stem, c)
                for f in (
                    sorted((p / DIR_SKILLS).glob("*.md")) if (p / DIR_SKILLS).is_dir() else []
                )
                for c in [_r(f)]
                if c
            ]
            return out
        for d in sorted(root.iterdir()) if root.exists() else []:
            if (
                d.is_dir()
                and not d.name.startswith(".")
                and (d / DIR_SKILLS / f"{name}.md").exists()
            ):
                c = _r(d / DIR_SKILLS / f"{name}.md")
                if c:
                    return [(name, c)]
    return []


_mem_cache: dict[str, Any] = {}


def _mem(base: Path):
    key = str(base)
    if key not in _mem_cache:
        db = _dataset.connect(f"sqlite:///{base}/memory.db", row_type=dict)
        if "entities" not in db:
            db.create_table("entities")
        _mem_cache[key] = db
    return _mem_cache[key]


def _mem_close():
    """Close all cached database connections."""
    for db in _mem_cache.values():
        with contextlib.suppress(Exception):
            db.close()
    _mem_cache.clear()


_MEDIA_DISPATCH = {k: (v[0], v[1]) for k, v in _C["media_dispatch"].items()}


class AdapterApprovalEngine:
    def __init__(self, wa, ctrl, timeout=120.0):
        self._wa = wa
        self._ctrl = ctrl
        self._timeout = timeout
        self._pending: dict[str, asyncio.Future] = {}
        orig = wa.on_poll_update

        async def _h(pid, tally):
            if pid in self._pending and not self._pending[pid].done():
                _yes = _C["approval"]["yes_keyword"]
                self._pending[pid].set_result(any(_yes in o.lower() for o in tally if tally[o]))
            if orig:
                await orig(pid, tally)

        wa.on_poll_update = _h

    async def request_approval(self, question: str, host_jid: str, detail: str = "") -> bool:
        if detail:
            await self._wa.send(host_jid, detail)
        pid = await self._wa.send_poll(host_jid, question, _C["approval"]["options"])
        self._pending[pid] = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(self._pending[pid], timeout=self._timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(pid, None)


class ChannelCore:
    def __init__(
        self,
        wa,
        ctrl,
        engine,
        notify,
        agent_dir=".",
        app_proxies=None,
        notify_queue=None,
        allowed_tools=None,
        allowed_resources=None,
    ):
        self._wa = wa
        self._ctrl = ctrl
        self._engine = engine
        self._notify = notify
        self._base = Path(agent_dir)
        self._app_proxies = app_proxies or {}
        self._notify_queue = notify_queue
        self._allowed_tools: set[str] | None = allowed_tools
        self._allowed_resources: list[str] | None = allowed_resources
        self._catchup_buffer: list[Message] = []
        self._approval = AdapterApprovalEngine(wa, ctrl, timeout=_cfg.test_message_timeout)
        self._pending_elevations: set[str] = set()
        engine.on_catchup = self._flush_catchup

    def _check_memory_access(self, a: dict) -> list[TextContent] | None:
        if self._allowed_resources is None:
            return None
        from fnmatch import fnmatch

        app_filter = a.get("app") or a.get("entity", {}).get("app", "")
        if not app_filter:
            return _R(_MSG["error_memory_denied"].format(app="(all)"))
        uri = f"c3://memory/{app_filter}"
        if not any(
            fnmatch(uri, p) or fnmatch(uri + "/", p) or fnmatch(uri + "/*", p)
            for p in self._allowed_resources
        ):
            return _R(_MSG["error_memory_denied"].format(app=app_filter))
        return None

    async def call_tool(self, name: str, arguments: dict) -> list[TextContent]:
        a = arguments or {}
        if name == "send_private":
            name = "reply"
        log(LOG_TOOL, f"{name} {json.dumps(a)[: _cfg.log_truncate]}")
        if self._allowed_tools is not None and name not in self._allowed_tools:
            log(LOG_POLICY, f"blocked tool: {name} (not in allowed_tools)")
            return _R(_MSG["error_tool_not_allowed"].format(name=name))
        if name in ("memory_read", "memory_search", "memory_write", "memory_delete"):
            err = self._check_memory_access(a)
            if err:
                return err
        if name in _MEDIA_DISPATCH:
            return await self._tool_send_media(name, a)
        handler = getattr(self, f"_tool_{name}", None)
        if not handler:
            return _R(_MSG["error_unknown_tool"].format(name=name))
        return await handler(a)

    async def _send_with_approval(self, jid: str, text: str) -> list[TextContent]:
        if self._ctrl.is_host(jid) or self._wa.is_group_id(jid) or self._ctrl.is_elevated(jid):
            await self._wa.send(jid, text)
            return _R("sent")
        if not self._ctrl.is_participant(jid):
            return _R(_MSG["error_not_in_session"])
        token = self._ctrl.mask(jid)
        host_jid = self._ctrl.unmask("host")
        pl = _cfg.dm_preview_length
        preview = text[:pl] + ("..." if len(text) > pl else "")
        if await self._approval.request_approval(
            _MSG["approval_dm_question"].format(token=token),
            host_jid,
            _MSG["approval_dm_detail"].format(token=token, preview=preview),
        ):
            await self._wa.send(jid, text)
            self._ctrl.grant_jid(ROLE_ELEVATED, jid)
            return _R("sent")
        return _R(_MSG["denied_dm"].format(token=token))

    async def _tool_reply(self, a: dict) -> list[TextContent]:
        jid = self._ctrl.unmask(pick(a, "jid", "to", "recipient") or TOKEN_HOST)
        text = pick(a, "text", "message", "content") or ""
        if not text.strip():
            return _R("Error: text required")
        return await self._send_with_approval(jid, text)

    async def _tool_send_poll(self, a: dict) -> list[TextContent]:
        question = a.get("question", "")
        if not question:
            return _R(_MSG["error_question_required"])
        options = a.get("options", [])
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except (ValueError, json.JSONDecodeError):
                options = []
        if not isinstance(options, list) or len(options) < 2:
            return _R(_MSG["error_options_invalid"])
        group_jid = self._ctrl.unmask(pick(a, "group_jid", "jid", "to") or TOKEN_GROUP)
        if not self._wa.is_group_id(group_jid):
            return _R(_MSG["error_group_not_resolved"])
        poll_id = await self._wa.send_poll(group_jid, question, options)
        self._engine.track_poll(poll_id, group_jid, a["question"])
        return _R(f"poll: {poll_id}")

    async def _tool_send_media(self, name: str, a: dict) -> list[TextContent]:
        jid = self._ctrl.unmask(pick(a, "jid", "to") or TOKEN_HOST)
        path = a.get("path") or ""
        if not path:
            return _R(_MSG["error_path_required"])
        if not Path(path).exists():
            return _R(_MSG["error_file_not_found"].format(path=path))
        method_name, keys = _MEDIA_DISPATCH[name]
        defaults = _C["media_defaults"]
        try:
            kwargs = {k: a.get(k, defaults.get(k, "")) for k in keys}
            # Bug #27: coerce ptt to bool
            if "ptt" in kwargs:
                kwargs["ptt"] = str(kwargs["ptt"]).lower() in ("true", "1", "yes")
            await getattr(self._wa, method_name)(jid, path, **kwargs)
        except NotImplementedError:
            return _R(_MSG["error_media_not_supported"])
        return _R("sent")

    async def _tool_react(self, a: dict) -> list[TextContent]:
        jid = self._ctrl.unmask(pick(a, "jid", "to") or TOKEN_HOST)
        mid = a.get("message_id") or ""
        emoji = a.get("emoji") or "👍"
        if not mid:
            return _R(_MSG["error_message_id_required"])
        try:
            await self._wa.react(jid, mid, emoji)
        except NotImplementedError:
            return _R(_MSG["error_reactions_not_supported"])
        return _R("reacted")

    async def _tool_get_group_members(self, a: dict) -> list[TextContent]:
        group_jid = self._ctrl.unmask(pick(a, "group_jid", "jid") or TOKEN_GROUP)
        if not self._wa.is_group_id(group_jid):
            return _R(_MSG["error_group_not_resolved"])
        members = await self._wa.get_group_members(group_jid)
        return _R(
            "\n".join(f"{m.name}{' (admin)' if m.is_admin else ''}" for m in members)
            or "No members"
        )

    async def _tool_resolve_group(self, a: dict) -> list[TextContent]:
        link = pick(a, "invite_link", "link") or ""
        if not link or not self._wa.is_valid_invite(link):
            return _R(_MSG["error_invalid_invite"])
        try:
            group_jid = await self._wa.resolve_group(link)
        except Exception as e:
            return _R(f"Error: {e}")
        members = await self._wa.get_group_members(group_jid)
        self._ctrl.grant(ROLE_GROUP, [{"jid": group_jid, "token": "group"}])
        if members:
            entries = [
                e
                for m in members
                for e in [{"jid": m.jid, "token": m.name}]
                + ([{"jid": m.lid, "token": m.name}] if m.lid else [])
            ]
            self._ctrl.grant(ROLE_PARTICIPANTS, entries)
        self._engine.set_active(group_jid, "session")
        log(LOG_C3, f"resolved group: {group_jid} ({len(members)} members)")
        ml = (
            "\n".join(f"{m.name}{' (admin)' if m.is_admin else ''}" for m in members)
            or "No members"
        )
        return _R(f"GROUP: group\nMEMBERS ({len(members)}):\n{ml}")

    async def _tool_set_timer(self, a: dict) -> list[TextContent]:
        seconds = parse_duration(pick(a, "seconds", "duration", "time"), _cfg.default_phase_timer)
        timer_name = pick(a, "name", "phase_name", "phase") or "timer"
        group_jid = self._engine.resolve_group(pick(a, "group_jid", "jid", "group"))
        if not group_jid:
            return _R(_MSG["error_no_active_session"])
        self._engine.set_phase_timer(group_jid, seconds, timer_name)
        return _R(f"timer: {timer_name} ({seconds}s)")

    async def _tool_end_session(self, a: dict) -> list[TextContent]:
        group_jid = pick(a, "group_jid", "jid")
        if group_jid:
            group_jid = self._ctrl.unmask(group_jid)
        self._engine.clear_all_timers()
        self._engine.clear_active(group_jid)
        self._ctrl.revoke(ROLE_PARTICIPANTS)
        self._ctrl.revoke(ROLE_GROUP)
        self._ctrl.revoke(ROLE_ELEVATED)
        self._pending_elevations.clear()
        return _R("session ended")

    async def _tool_memory_write(self, a: dict) -> list[TextContent]:
        entity = a.get("entity") or {}
        if not entity:
            return _R(_MSG["error_entity_required"])
        if not all(k in entity for k in ("app", "entity", "name")):
            return _R(_MSG["error_entity_fields"])
        # Bug #76: validate entity keys against known schema fields
        entity = {k: v for k, v in entity.items() if k in ENTITY_KEYS}
        _mem(self._base)["entities"].upsert(entity, ["app", "entity", "name"])
        return _R("ok")

    async def _tool_memory_read(self, a: dict) -> list[TextContent]:
        kwargs = {k: a[k] for k in ("app", "entity_type") if a.get(k)}
        if "entity_type" in kwargs:
            kwargs["entity"] = kwargs.pop("entity_type")
        return _R(json.dumps(list(_mem(self._base)["entities"].find(**kwargs)), indent=2))

    async def _tool_memory_search(self, a: dict) -> list[TextContent]:
        q = (a.get("query") or "").strip()
        if not q:
            return _R("[]")
        db = _mem(self._base)
        try:
            table = db["entities"]
            cols = [c for c in table.columns if c != "id"]
            # Bug #29: escape LIKE wildcards
            esc = "\\"
            q_escaped = q.replace("%", esc + "%").replace("_", esc + "_")
            where = " OR ".join(f'CAST("{c}" AS TEXT) LIKE :q ESCAPE :esc' for c in cols)
            rows = list(
                db.query(f"SELECT * FROM entities WHERE {where}", q=f"%{q_escaped}%", esc=esc)
            )
        except Exception:
            rows = [r for r in db["entities"].all() if q.lower() in json.dumps(r).lower()]
        return _R(json.dumps(rows, indent=2))

    async def _tool_memory_delete(self, a: dict) -> list[TextContent]:
        kwargs = {k: a[k] for k in ("app", "entity_type", "name") if a.get(k)}
        if "entity_type" in kwargs:
            kwargs["entity"] = kwargs.pop("entity_type")
        if not kwargs:
            return _R(_MSG["error_delete_filter_required"])
        _mem(self._base)["entities"].delete(**kwargs)
        return _R("ok")

    async def _tool_load_app(self, a: dict) -> list[TextContent]:
        pname = (a.get("name") or "").strip().lower().removesuffix(".md")
        if not pname:
            return _R(_MSG["error_name_required"])
        skills = find_app_content(pname, self._base)
        if not skills:
            return _R(_MSG["error_app_not_found"].format(name=pname))
        for sn, content in skills:
            await self._notify(content, {"type": EVT_SKILL_LOAD, "skill": sn})
        msgs = [f"Skills loaded: {', '.join(s for s, _ in skills)}"]
        if pname not in self._app_proxies:
            mcp_file = next(
                (
                    root / pname / ".mcp"
                    for root in [self._base, _PKG / DIR_APPS]
                    if (root / pname / ".mcp").exists()
                ),
                None,
            )
            if mcp_file:
                try:
                    proxy = AppMCPProxy(pname, json.loads(mcp_file.read_text()), self._base)
                    self._app_proxies[pname] = proxy
                    asyncio.ensure_future(proxy.run())
                    try:
                        await asyncio.wait_for(proxy._ready.wait(), timeout=_cfg.app_init_timeout)
                        msgs.append(f"MCP server: {len(proxy.tools)} tools added")
                    except asyncio.TimeoutError:
                        msgs.append("MCP server starting...")
                    if self._notify_queue:
                        await self._notify_queue.put(("", {EVT_TOOLS_CHANGED: True}))
                except Exception as e:
                    log(LOG_APP, f"failed to load MCP for '{pname}': {e}")
        return _R("\n".join(msgs))

    _tool_load_agent = _tool_load_app

    async def _tool_save_file(self, a: dict) -> list[TextContent]:
        rel_path = pick(a, "path", "filename") or ""
        if not rel_path:
            return _R(_MSG["error_path_required"])
        target = (self._base / rel_path).resolve()
        base_r = self._base.resolve()
        # Bug fix #1: string prefix check allows /app_evil to pass /app
        # Bug fix #5: resolve symlinks to prevent symlink bypass
        try:
            target.relative_to(base_r)
        except ValueError:
            return _R(_MSG["error_path_outside"])
        content = a.get("content", "")
        if len(content) > 1_000_000:
            return _R("Error: content too large (max 1MB)")
        # Block overwriting critical config files
        _protected = PROTECTED_FILES
        if target.name in _protected:
            return _R(f"Error: cannot overwrite protected file: {target.name}")
        if target.suffix == ".md":
            with contextlib.suppress(ValueError):
                p = target.relative_to(base_r).parts
                if len(p) >= 2 and (self._base / p[0] / FILE_APP_JSON).exists():
                    if not (
                        (len(p) == 2 and p[1] == FILE_CLAUDE_MD)
                        or (len(p) == 3 and p[1] == DIR_SKILLS)
                    ):
                        return _R(_MSG["error_md_restricted"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return _R(f"saved: {rel_path}")

    async def _flush_catchup(self) -> None:
        host = self._ctrl.unmask("host")
        if not self._catchup_buffer:
            await self._wa.send(host, _MSG["catchup_empty"])
            return
        msgs, self._catchup_buffer = self._catchup_buffer, []
        n = len(msgs)
        tl = _cfg.catchup_text_length
        body = _MSG["catchup_header"].format(count=n) + "\n".join(
            f"  [{self._ctrl.mask(m.sender) if self._ctrl.is_known(m.sender) else m.push_name}]"
            f"{f' [{m.media_type}]' if m.media_type else ''} {m.text[:tl]}"
            for m in msgs
        )
        await self._notify(body, {"type": "catchup", "count": str(n)})
        await self._wa.send(host, _MSG["catchup_confirm"].format(count=n))
        log("catchup", f"flushed {n}")

    async def on_message(self, msg: Message) -> None:
        tag = "catchup" if msg.catchup else "msg"
        log(
            tag,
            f"from={msg.push_name} jid={msg.sender} group={msg.is_group} text={msg.text[: _cfg.log_text_length]}",
        )
        if msg.catchup:
            self._catchup_buffer.append(msg)
            return
        if not self._ctrl.can_reach(msg.sender, msg.is_group, msg.text):
            if msg.is_group and self._ctrl.has_role(msg.jid, ROLE_GROUP):
                name = msg.push_name or self._wa.extract_name(msg.sender)
                self._ctrl.grant_jid(ROLE_PARTICIPANTS, msg.sender)
                self._ctrl.register(msg.sender, name)
                log(LOG_POLICY, f"auto-admitted: {name} ({msg.sender})")
            elif not msg.is_group and self._ctrl.is_elevated(msg.sender):
                log(LOG_POLICY, f"elevated participant DM: {msg.push_name}")
            elif not msg.is_group and self._ctrl.is_participant(msg.sender):
                if msg.sender not in self._pending_elevations:
                    self._pending_elevations.add(msg.sender)
                    name = msg.push_name or self._wa.extract_name(msg.sender)
                    host_jid = self._ctrl.unmask("host")
                    approved = await self._approval.request_approval(
                        _MSG["approval_elevation_question"].format(name=name),
                        host_jid,
                        _MSG["approval_elevation_detail"].format(name=name),
                    )
                    self._pending_elevations.discard(msg.sender)
                    if approved:
                        self._ctrl.grant_jid(ROLE_ELEVATED, msg.sender)
                        log(LOG_POLICY, f"elevated: {msg.sender}")
                return
            else:
                log(LOG_POLICY, f"dropped: {msg.sender}")
                return
        if await self._engine.handle(msg):
            return
        _san = _C["sanitize"]
        sanitized = re.sub(
            _san["injection_regex"],
            _san["injection_replace"],
            re.sub(_san["tag_regex"], "", msg.text),
        )
        is_host = self._ctrl.has_role(msg.sender, ROLE_HOSTS)
        role_tag = "host" if is_host else "participant"
        meta: dict = {
            "type": EVT_MESSAGE,
            "jid": self._ctrl.mask(msg.jid),
            "sender": self._ctrl.mask(msg.sender),
            "name": msg.push_name or "",
            "role": role_tag,
        }
        if msg.is_group:
            meta["group"] = "true"
        if msg.is_group and not is_host:
            meta["read_only"] = "true"
        if msg.media_path:
            meta.update(
                {
                    "media_path": msg.media_path,
                    "media_type": msg.media_type or "",
                    **{
                        k: str(v)
                        for k, v in {
                            "media_mimetype": msg.media_mimetype,
                            "media_size": msg.media_size,
                            "media_duration": msg.media_duration,
                            "media_filename": msg.media_filename,
                        }.items()
                        if v is not None
                    },
                }
            )
        await self._notify(
            f"[{role_tag}] {self._ctrl.mask(msg.sender)}: {self._ctrl.mask(sanitized)}", meta
        )


_dm = _C["manifest"]
_DEFAULT_MANIFEST = AppManifest(name=_dm["name"], access=AccessPolicy(**_dm["access"]))


def _merge_manifests(extras: list[dict]) -> AppManifest:
    dm: set[str] = set()
    group: set[str] = set()
    commands: dict[str, set[str]] = {}
    for m in [_DEFAULT_MANIFEST, *extras]:
        access = (
            m.access
            if isinstance(m, AppManifest)
            else AccessPolicy(
                **{
                    "commands": m.get("access", {}).get("commands", {}),
                    "dm": m.get("access", {}).get("dm", []),
                    "group": m.get("access", {}).get("group", []),
                }
            )
        )
        dm.update(access.dm)
        group.update(access.group)
        for cmd, roles in access.commands.items():
            commands.setdefault(cmd, set()).update(roles)
    names = [(m.name if isinstance(m, AppManifest) else m.get("name", "")) for m in extras]
    return AppManifest(
        name="+".join(n for n in names if n) or "c3",
        access=AccessPolicy(
            dm=list(dm), group=list(group), commands={k: list(v) for k, v in commands.items()}
        ),
    )


_BRIDGE_APP = Path("/app/c3/baileys_bridge.js")
_BRIDGE = _BRIDGE_APP if _BRIDGE_APP.exists() else _PKG / "baileys_bridge.js"

_MEDIA_FIELD_MAP = {
    "media_path": "mediaPath",
    "media_type": "mediaType",
    "media_mimetype": "mediaMimetype",
    "media_size": "mediaSize",
    "media_duration": "mediaDuration",
    "media_filename": "mediaFileName",
}


class BaileysAdapter(ChatAdapter):
    def __init__(self, bridge: str | None = None, sessions_dir: str | None = None):
        self._bridge = bridge or str(_BRIDGE)
        self._sessions = sessions_dir
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        self._next_id = 1
        self.admin_jid = ""
        self._env: dict = {}
        self._cwd = ""
        self._bridge_restarts = 0
        self._shutting_down = False
        self._bridge_stderr = None

    def _bg(self, coro):
        t = asyncio.ensure_future(coro)
        self._bg_tasks.add(t)

        def _done(task):
            self._bg_tasks.discard(task)
            if task.cancelled():
                return
            exc = task.exception()
            if exc:
                log(LOG_BAILEYS, f"background task error: {exc}")

        t.add_done_callback(_done)

    async def connect(self) -> None:
        bd = Path(self._bridge).parent
        await self._ensure_node_modules(bd)
        # Bug #37: whitelist env vars for bridge process
        _safe_keys = {"PATH", "HOME", "NODE_PATH", "NODE_ENV", "LANG", "TERM"}
        self._env = {k: v for k, v in os.environ.items() if k in _safe_keys}
        if self._sessions:
            self._env["SESSIONS_DIR"] = self._sessions
        self._cwd = str(
            next((c for c in [bd, bd.parent, Path("/app")] if (c / "node_modules").is_dir()), bd)
        )
        await self._start_bridge()

    async def _start_bridge(self) -> None:
        if self._bridge_stderr:
            with contextlib.suppress(Exception):
                self._bridge_stderr.close()
        bl = Path(self._env.get("SESSIONS_DIR", ".")) / ".." / DIR_LOGS / "bridge.log"
        bl.parent.mkdir(parents=True, exist_ok=True)
        self._bridge_stderr = open(bl, "ab")
        self._proc = await asyncio.create_subprocess_exec(
            "node",
            self._bridge,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._bridge_stderr,
            env=self._env,
            cwd=self._cwd,
        )
        log(LOG_BAILEYS, f"bridge started (pid={self._proc.pid})")
        asyncio.ensure_future(self._read_events())
        asyncio.ensure_future(self._watch_and_restart())
        ready = asyncio.get_running_loop().create_future()
        # Bug #41: store original callback once, don't nest wrappers
        if not hasattr(self, "_orig_on_ready"):
            self._orig_on_ready = self.on_ready

        async def _on_ready():
            if not ready.done():
                ready.set_result(None)
            if self._orig_on_ready:
                await self._orig_on_ready()

        self.on_ready = _on_ready
        await asyncio.wait_for(ready, timeout=_cfg.bridge_connect_timeout)
        self._bridge_restarts = 0

    async def _watch_and_restart(self) -> None:
        if not self._proc:
            return
        await self._proc.wait()
        if self._shutting_down or self._bridge_restarts >= _C["bridge"]["max_restarts"]:
            return
        delay = _C["bridge"]["backoff_base"] * (2**self._bridge_restarts)
        self._bridge_restarts += 1
        log(LOG_BAILEYS, f"restarting in {delay}s (attempt {self._bridge_restarts})")
        await asyncio.sleep(delay)
        try:
            await self._start_bridge()
        except Exception as e:
            log(LOG_BAILEYS, f"restart failed: {e}")

    async def send(self, jid: str, text: str) -> None:
        await self._cmd(cmd="send", jid=jid, text=text)

    async def send_poll(self, jid: str, question: str, options: list[str]) -> str:
        return str(await self._cmd(cmd="sendPoll", jid=jid, question=question, options=options))

    async def resolve_group(self, invite_link: str) -> str:
        return str(await self._cmd(cmd="resolveGroup", link=invite_link))

    async def get_group_members(self, group_jid: str) -> list[GroupMember]:
        result = await self._cmd(cmd="getGroupMembers", groupJid=group_jid)
        return [
            GroupMember(jid=m["jid"], name=m["name"], is_admin=m["isAdmin"], lid=m.get("lid"))
            for m in (result or [])
        ]

    async def react(self, jid: str, message_id: str, emoji: str) -> None:
        await self._cmd(cmd="sendReaction", jid=jid, messageId=message_id, emoji=emoji)

    async def send_presence(self, jid: str, presence: str = "composing") -> None:
        await self._cmd(cmd="sendPresence", jid=jid, presence=presence)

    async def send_image(self, jid: str, path: str, caption: str = "") -> None:
        await self._cmd(cmd="sendImage", jid=jid, path=path, caption=caption)

    async def send_video(self, jid: str, path: str, caption: str = "") -> None:
        await self._cmd(cmd="sendVideo", jid=jid, path=path, caption=caption)

    async def send_audio(self, jid: str, path: str, ptt: bool = False) -> None:
        await self._cmd(cmd="sendAudio", jid=jid, path=path, ptt=ptt)

    async def send_document(
        self, jid: str, path: str, filename: str = "", mimetype: str = ""
    ) -> None:
        await self._cmd(
            cmd="sendDocument", jid=jid, path=path, fileName=filename, mimetype=mimetype
        )

    def get_name(self, jid: str) -> str:
        return jid.split("@")[0]

    def is_group_id(self, id: str) -> bool:
        return id.endswith("@g.us")

    def is_valid_invite(self, link: str) -> bool:
        from urllib.parse import urlparse

        try:
            h = urlparse(link).hostname or ""
            return h == "chat.whatsapp.com" or h.endswith(".chat.whatsapp.com")
        except Exception:
            return False

    def extract_name(self, id: str) -> str:
        return id.split("@")[0]

    @staticmethod
    async def _ensure_node_modules(bridge_dir: Path) -> None:
        d = next(
            (d for d in [bridge_dir, *bridge_dir.parents] if (d / "package.json").exists()),
            bridge_dir,
        )
        if (d / "node_modules").exists() or not (d / "package.json").exists():
            return
        log(LOG_BAILEYS, f"installing node deps in {d}...")
        proc = await asyncio.create_subprocess_exec(
            "npm",
            "install",
            "--production",
            "--ignore-scripts",
            "--silent",
            cwd=str(d),
            stderr=sys.stderr,
        )
        if await proc.wait() != 0:
            raise RuntimeError("npm install failed — is Node.js installed?")

    async def _cmd(self, **kw) -> object:
        if not self._proc or not self._proc.stdin or self._proc.returncode is not None:
            raise BaileysDisconnectedError("bridge not connected")
        cid = self._next_id
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[cid] = fut
        try:
            self._proc.stdin.write((json.dumps({"id": cid, **kw}) + "\n").encode())
            await self._proc.stdin.drain()
            return await asyncio.wait_for(fut, timeout=_cfg.baileys_cmd_timeout)
        except asyncio.TimeoutError:
            raise BaileysTimeoutError(f"bridge command timed out: {kw.get('cmd')}")
        except Exception:
            raise
        finally:
            self._pending.pop(cid, None)

    async def _read_events(self) -> None:
        assert self._proc and self._proc.stdout
        async for raw_line in self._proc.stdout:
            line = raw_line.decode().strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                log(LOG_BAILEYS, f"bad JSON: {line[: _cfg.log_truncate]}")
                continue
            try:
                if "id" in obj:
                    fut = self._pending.pop(obj["id"], None)
                    if fut and not fut.done():
                        if "error" in obj:
                            fut.set_exception(RuntimeError(obj["error"]))
                        else:
                            fut.set_result(obj.get("result"))
                    continue
                event = obj.get("event")
                if event == "ready":
                    self.admin_jid = obj.get("adminJid", "")
                    log(LOG_BAILEYS, f"ready, admin={self.admin_jid}")
                    if self.on_ready:
                        await self.on_ready()
                elif event == "message":
                    raw = obj.get("msg")
                    if not raw or not all(k in raw for k in ("jid", "sender", "text", "isGroup")):
                        log(LOG_BAILEYS, f"malformed message: {json.dumps(raw)[:100]}")
                        continue
                    msg = Message(
                        jid=raw["jid"],
                        sender=raw["sender"],
                        text=raw["text"],
                        push_name=raw.get("pushName") or raw["sender"].split("@")[0],
                        timestamp=raw.get("timestamp", 0),
                        is_group=raw["isGroup"],
                        message_id=raw.get("messageId"),
                        catchup=bool(raw.get("catchup")),
                        **{
                            k: raw.get(v)
                            for k, v in _MEDIA_FIELD_MAP.items()
                            if raw.get(v) is not None
                        },
                    )
                    if self.on_message:
                        self._bg(self.on_message(msg))
                elif event == "media_ready":
                    log(LOG_MEDIA, f"downloaded: {obj.get('mediaType')}")
                elif event == EVT_POLL_UPDATE and self.on_poll_update:
                    pid, tally = obj.get("pollId"), obj.get("tally")
                    if pid and tally is not None:
                        self._bg(self.on_poll_update(pid, tally))
                    else:
                        log(LOG_BAILEYS, f"malformed poll_update: {json.dumps(obj)[:100]}")
            except (KeyError, ValueError, TypeError) as e:
                log(LOG_BAILEYS, f"event handler error: {e}")
            except Exception as e:
                log(LOG_BAILEYS, f"unexpected event error: {e}")
                raise
        log(LOG_BAILEYS, "stdout closed — bridge disconnected")


def _parse_resource_uri(uri_str: str) -> tuple[str, list[str]]:
    path = uri_str.replace("c3://", "").strip("/")
    if not path:
        return "", []
    p = path.split("/", 1)
    return p[0], p[1].split("/") if len(p) > 1 else []


async def create_channel(
    wa: ChatAdapter,
    agent_dir: str = ".",
    transport: str = "stdio",
    host: str = "0.0.0.0",
    port: int = 3000,
) -> None:
    base = Path(agent_dir)
    bundled = _PKG / DIR_APPS
    setup_logging(base)
    sid = str(uuid.uuid4())
    sat = datetime.now(timezone.utc).isoformat()
    sd = base / DIR_SESSIONS
    sd.mkdir(parents=True, exist_ok=True)
    try:
        (sd / "current.json").write_text(
            json.dumps(
                {"session_id": sid, "started_at": sat, "agent_dir": str(base.resolve())}, indent=2
            )
        )
    except OSError as e:
        log(LOG_ERROR, f"failed to write session file: {e}")
    log(LOG_C3, f"session {sid} started")

    def _read(rel):
        return next((p.read_text() for d in [base, bundled] for p in [d / rel] if p.exists()), None)

    raw_cfg = _read_json(base / "config.json")
    config = (
        AppConfig(
            hosts=[HostConfig(**h) for h in raw_cfg.get("hosts", [])],
            admins=[HostConfig(**a) for a in raw_cfg.get(ROLE_ADMINS, [])],
        )
        if raw_cfg
        else AppConfig()
    )
    parts: list[str] = []
    claude_md = _read(FILE_CLAUDE_MD)
    if claude_md:
        parts.append(claude_md)
    notify_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=_cfg.notify_queue_size)

    async def notify(content: str, meta: dict) -> None:
        try:
            notify_queue.put_nowait((content, meta))
        except asyncio.QueueFull:
            log(LOG_ERROR, "notify queue full, dropping message")

    memory_schemas: dict = {}
    raw_manifests: list[dict] = []
    seen_pj: set[str] = set()
    _scheduler = AsyncIOScheduler()
    for _name, cat_dir in _scan_dirs(base, bundled):
        _has_content = (cat_dir / FILE_CLAUDE_MD).exists() or (cat_dir / DIR_SKILLS).is_dir()
        _has_config = any((cat_dir / f).exists() for f in (FILE_APP_JSON, FILE_AGENT_JSON))
        if _has_content and not _has_config and cat_dir.parent == base:
            _ensure_safe_app_json(cat_dir, _name)
            log(LOG_APP, f"auto-created app.json for {_name}")
        pj_data = _load_manifest(cat_dir)
        if not pj_data:
            continue
        pj_key = str(
            next(
                (cat_dir / f for f in (FILE_APP_JSON, FILE_AGENT_JSON) if (cat_dir / f).exists()),
                "",
            )
        )
        if not pj_key:
            continue
        schema = pj_data.get("memory_schema", {})
        if schema:
            memory_schemas.update(schema)
        if pj_key not in seen_pj:
            seen_pj.add(pj_key)
            raw_manifests.append(pj_data)
        for job in pj_data.get("crons", []):
            try:

                async def _fire(j=job, p=_name):
                    await notify(json.dumps({"job": j["job"], "app": p}), {"type": EVT_CRON_TICK})

                _scheduler.add_job(
                    _fire,
                    CronTrigger.from_crontab(job["schedule"]),
                    id=f"{_name}:{job['job']}",
                    replace_existing=True,
                )
            except Exception as e:
                log(LOG_CRON, f"bad cron in {_name} (schedule={job.get('schedule', '?')}): {e}")
    for pj in (
        r / f for r in [base, bundled] for f in (FILE_APP_JSON, FILE_AGENT_JSON) if (r / f).exists()
    ):
        if str(pj) not in seen_pj:
            seen_pj.add(str(pj))
            d = _read_json(pj)
            if d:
                raw_manifests.append(d)
    if memory_schemas:
        parts.append(f"## Memory Schema\n\n```json\n{json.dumps(memory_schemas, indent=2)}\n```")
    instructions = "\n\n---\n\n".join(parts)
    _app_proxies: dict[str, AppMCPProxy] = {}
    manifest = _merge_manifests(raw_manifests)
    ctrl = AccessControl(manifest, config)
    _per_app_tools: dict[str, set[str]] = {}
    _all_allowed: set[str] = set()
    _all_res: set[str] = set()
    _has_builtin = False
    for rm in raw_manifests:
        name, at, ar = (
            rm.get("name", ""),
            rm.get("allowed_tools", []),
            rm.get("allowed_resources", []),
        )
        is_builtin = rm.get("trust_level") == TRUST_BUILTIN
        if at:
            _per_app_tools[name] = set(at)
            _all_allowed.update(at)
        elif not is_builtin:
            _per_app_tools[name] = {"reply", "send_poll"}
        if not at and is_builtin:
            _has_builtin = True
        if ar:
            _all_res.update(ar)
        elif is_builtin:
            _all_res.update(["c3://schema/*", "c3://memory/*", "c3://media/*"])
    allowed_tools_set: set[str] | None = None if _has_builtin else (_all_allowed or None)
    allowed_res_patterns: list[str] | None = list(_all_res) or None
    if allowed_res_patterns:
        log(LOG_POLICY, f"allowed_resources: {allowed_res_patterns}")
    engine = SessionEngine(wa, notify, ctrl, agent_dir=base)
    core = ChannelCore(
        wa,
        ctrl,
        engine,
        notify,
        base,
        _app_proxies,
        notify_queue,
        allowed_tools=allowed_tools_set,
        allowed_resources=allowed_res_patterns,
    )
    server = Server("c3", instructions=instructions or None)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        tools = [
            Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
            for t in BASE_TOOLS
        ]
        for proxy in _app_proxies.values():
            tools.extend(proxy.tools)
        return tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[TextContent]:
        for px in _app_proxies.values():
            if name in px.tool_names:
                aa = _per_app_tools.get(px.name)
                if aa is not None and name not in aa:
                    return _R(f"Error: tool '{name}' not allowed for app '{px.name}'")
                return await px.call_tool(name, arguments)
        return await core.call_tool(name, arguments)

    _media_dir = base / DIR_SESSIONS / "media"

    @server.list_resources()
    async def _list_resources() -> list[Resource]:
        resources = [
            Resource(
                name="app-schema",
                uri="c3://schema/app",
                description="JSON Schema for app.json",
                mimeType="application/json",
            )
        ]
        with contextlib.suppress(Exception):
            resources.extend(
                Resource(
                    name=f"memory-{r['app']}",
                    uri=f"c3://memory/{r['app']}",
                    description=f"All {r['app']} memory entities",
                    mimeType="application/json",
                )
                for r in _mem(base)["entities"].distinct("app")
            )
        if _media_dir.exists():
            resources.extend(
                Resource(
                    name=f"media-{f.stem}",
                    uri=f"c3://media/{f.stem}",
                    description=f"Media file: {f.name}",
                    mimeType=_MIME_TYPES.get(f.suffix.lstrip("."), "application/octet-stream"),
                    size=f.stat().st_size,
                )
                for f in sorted(_media_dir.iterdir())
                if f.is_file() and f.stat().st_size > 0
            )
        return resources

    @server.list_resource_templates()
    async def _list_resource_templates() -> list[ResourceTemplate]:
        return [ResourceTemplate(**t) for t in _C["resource_templates"]]

    @server.read_resource()
    async def _read_resource(uri) -> list[TextResourceContents | BlobResourceContents]:
        uri_str = str(uri)

        def _err(t):
            return [TextResourceContents(uri=uri, text=t, mimeType="text/plain")]

        if allowed_res_patterns is not None:
            from fnmatch import fnmatch

            if not any(fnmatch(uri_str, p) for p in allowed_res_patterns):
                log(LOG_POLICY, f"blocked resource: {uri_str}")
                return _err(f"Error: access denied to {uri_str}")
        scheme, rparts = _parse_resource_uri(uri_str)
        if scheme == "schema":
            return [
                TextResourceContents(
                    uri=uri,
                    text=json.dumps(AppManifest.model_json_schema(), indent=2),
                    mimeType="application/json",
                )
            ]
        if scheme == "memory":
            kwargs: dict = {"app": rparts[0]} if rparts else {}
            if len(rparts) > 1:
                kwargs["entity"] = rparts[1]
            return [
                TextResourceContents(
                    uri=uri,
                    text=json.dumps(list(_mem(base)["entities"].find(**kwargs)), indent=2),
                    mimeType="application/json",
                )
            ]
        if scheme == "media":
            import base64

            msg_id = rparts[0] if rparts else ""
            f = next(
                (
                    f
                    for f in (_media_dir.iterdir() if _media_dir.exists() else [])
                    if f.stem == msg_id and f.is_file()
                ),
                None,
            )
            if f:
                return [
                    BlobResourceContents(
                        uri=uri,
                        blob=base64.b64encode(f.read_bytes()).decode(),
                        mimeType=_MIME_TYPES.get(f.suffix.lstrip("."), "application/octet-stream"),
                    )
                ]
            return _err("Error: media not found")
        return _err(f"Error: unknown resource {uri_str}")

    wa.on_message = core.on_message

    async def _on_wa_ready() -> None:
        rf = base / DIR_SESSIONS / "restart_count"
        try:
            rc = int(rf.read_text().strip()) + 1 if rf.exists() else 1
        except (ValueError, OSError):
            rc = 1
        rf.write_text(str(rc))
        label = _MSG["session_fresh"] if rc == 1 else _MSG["session_resumed"].format(count=rc)
        body = _MSG["session_body"].format(label=label, sid=sid, sat=sat)
        await notify(
            body,
            {
                "type": EVT_SESSION_START,
                "session_id": sid,
                "started_at": sat,
                "restart_count": str(rc),
            },
        )

    wa.on_ready = _on_wa_ready

    def _notif(method, params):
        return SessionMessage(
            JSONRPCMessage(root=JSONRPCNotification(jsonrpc="2.0", method=method, params=params))
        )

    async def _drain_notifications(write_stream) -> None:
        while True:
            content, meta = await notify_queue.get()
            if meta.get(EVT_TOOLS_CHANGED):
                with contextlib.suppress(Exception):
                    await write_stream.send(_notif("notifications/tools/list_changed", {}))
                continue
            mc = ctrl.mask(content)
            mm = ctrl.mask_meta(meta) if meta else meta
            log(
                "notify",
                f"type={mm.get('type', '?')} {mc[: _cfg.log_truncate].replace(chr(10), ' ')}",
            )
            try:
                await write_stream.send(
                    _notif("notifications/claude/channel", {"content": mc, "meta": mm})
                )
            except Exception as e:
                log(LOG_ERROR, f"notify failed: {e}")

    _scheduler.start()
    import signal

    def _shutdown(*_args):
        log(LOG_C3, "shutting down...")
        _scheduler.shutdown(wait=False)
        if hasattr(wa, "_shutting_down"):
            wa._shutting_down = True
        if hasattr(wa, "_proc") and wa._proc:
            wa._proc.terminate()
        if hasattr(wa, "_bridge_stderr") and wa._bridge_stderr:
            with contextlib.suppress(Exception):
                wa._bridge_stderr.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    async def _watch_apps() -> None:
        dirs = [str(d) for d in [base, bundled] if d.exists()]
        if not dirs:
            return
        async for changes in awatch(*dirs):
            for _, path in changes:
                p = Path(path)
                if not path.endswith(".md"):
                    continue
                if (
                    p.name == FILE_CLAUDE_MD
                    and p.parent.parent == base
                    and not (p.parent / FILE_APP_JSON).exists()
                ):
                    _ensure_safe_app_json(p.parent, p.parent.name)
                    log(LOG_C3, f"auto-created app.json for {p.parent.name}")
                if not (
                    (p.name == FILE_CLAUDE_MD and p.parent.parent in (base, bundled))
                    or (p.parent.name == DIR_SKILLS and p.parent.parent.parent in (base, bundled))
                ):
                    continue
                with contextlib.suppress(OSError):
                    await notify(p.read_text(), {"type": EVT_SKILL_LOAD, "skill": p.stem})
                    log(LOG_C3, f"hot-reloaded: {p.name}")

    _init_opts = server.create_initialization_options(
        experimental_capabilities={"claude/channel": {}}
    )
    if transport == "sse":
        import uvicorn
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
        from starlette.responses import JSONResponse

        sset = SseServerTransport("/messages/")

        async def _sse(req):
            async with (
                sset.connect_sse(req.scope, req.receive, req._send) as (rs, ws),
                anyio.create_task_group() as tg,
            ):
                tg.start_soon(server.run, rs, ws, _init_opts)
                tg.start_soon(_drain_notifications, ws)
                tg.start_soon(_watch_apps)

        routes = [
            Route("/health", endpoint=lambda r: JSONResponse({"status": "ok"})),
            Route("/sse", endpoint=_sse),
            Mount("/messages/", app=sset.handle_post_message),
        ]
        if hasattr(wa, "starlette_routes"):
            routes.extend(wa.starlette_routes())
        app_ = Starlette(routes=routes)
        log(LOG_C3, f"MCP SSE server on {host}:{port}...")
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                uvicorn.Server(
                    uvicorn.Config(app_, host=host, port=port, log_level="warning")
                ).serve
            )
            tg.start_soon(wa.connect)
    else:
        log(LOG_C3, "MCP stdio server starting...")
        async with stdio_server() as (rs, ws), anyio.create_task_group() as tg:
            tg.start_soon(server.run, rs, ws, _init_opts)
            tg.start_soon(wa.connect)
            tg.start_soon(_drain_notifications, ws)
            tg.start_soon(_watch_apps)


def _find_app_dir(name: str) -> str | None:
    # Bug #63: validate name is alphanumeric + hyphens only
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        return None
    import importlib.util

    spec = importlib.util.find_spec(f"c3_{name.replace('-', '_')}")
    if spec and spec.origin:
        return str(Path(spec.origin).parent)
    for p in [_PKG / DIR_APPS / name, _PKG.parent.parent / f"c3-{name}"]:
        if p.is_dir() and any(
            (p / f).exists() for f in ("config.json", FILE_CLAUDE_MD, FILE_APP_JSON)
        ):
            return str(p)
    return None


def _build_apps_json(apps_root: Path) -> str:
    agents: dict = {}
    bundled = _PKG / DIR_APPS
    for name, d in _scan_dirs(apps_root, bundled):
        try:
            prompt_text = (d / FILE_CLAUDE_MD).read_text()
        except OSError:
            continue
        meta = _load_manifest(d)
        entry: dict = {"description": meta.get("description", name), "prompt": prompt_text}
        mcp_data = _read_json(d / "mcp.json")
        if mcp_data:
            ms = {
                sn: {
                    **sc,
                    **(
                        {"command": str((d / sc["command"]).resolve())}
                        if "command" in sc and not Path(sc["command"]).is_absolute()
                        else {}
                    ),
                }
                for sn, sc in mcp_data.get("mcpServers", {}).items()
            }
            if ms:
                entry["mcpServers"] = ms
        agents[name] = entry
    return json.dumps(agents)


def _launcher_mode(agent_dir: str, skip_permissions: bool, sse_url: str | None = None) -> None:
    import shutil

    if not shutil.which("claude"):
        sys.exit("Error: 'claude' CLI not found")
    base = Path(agent_dir).resolve()
    (base / DIR_LOGS).mkdir(exist_ok=True)
    mf = _ensure_mcp_json(base)
    if sse_url:
        mcfg = {"mcpServers": {"whatsapp": {"type": "sse", "url": sse_url}}}
        mf.write_text(json.dumps(mcfg, indent=2))
    (base / ".upstream.mcp.json").write_text(mf.read_text())
    agents_json = _build_apps_json(base)
    log(LOG_C3, f"registered apps: {', '.join(json.loads(agents_json).keys())}")
    sd = base / DIR_SESSIONS
    clear_flag = sd / "clear_session"
    if clear_flag.exists():
        clear_flag.unlink()
        log(LOG_C3, "fresh session (cleared by host)")
    claude_args = [
        "claude",
        "--model",
        _C["server"]["model"],
        "--mcp-config",
        str(mf),
        "--agents",
        agents_json,
        "--dangerously-skip-permissions",
        "--dangerously-load-development-channels",
        "server:whatsapp",
    ]
    log(LOG_C3, "launching Claude Code...")
    os.chdir(base)
    import subprocess
    import threading
    import time
    import pty
    import select

    _ansi_re = re.compile(
        rb"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][0-9A-B]|\x1b\[[\?]?[0-9;]*[hlm]"
    )
    master_fd, slave_fd = pty.openpty()
    lf = open(base / DIR_LOGS / "claude.log", "ab")
    try:
        proc = subprocess.Popen(
            claude_args, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True
        )
    except Exception:
        lf.close()
        os.close(master_fd)
        os.close(slave_fd)
        raise
    os.close(slave_fd)
    threading.Thread(
        target=lambda: [time.sleep(d) or os.write(master_fd, b"\r") for d in [3, 2, 2, 2, 2, 2]],
        daemon=True,
    ).start()
    try:
        while proc.poll() is None:
            r, _, _ = select.select([master_fd], [], [], 1.0)
            if not r:
                continue
            data = os.read(master_fd, 4096)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            clean = _ansi_re.sub(b"", data).replace(b"\r", b"")
            if clean.strip():
                lf.write(clean)
                lf.flush()
    except OSError:
        pass
    finally:
        lf.close()
        os.close(master_fd)
    sys.exit(proc.returncode or 0)


def _check_prereqs() -> list[str]:
    import shutil
    import subprocess

    issues = []
    _pre = _C["prereqs"]
    min_ver = _cfg.node_min_version
    if not shutil.which("node"):
        issues.append(_pre["checks"][0]["missing"])
    elif (
        int(
            subprocess.check_output(["node", "--version"], text=True)
            .strip()
            .lstrip("v")
            .split(".")[0]
        )
        < min_ver
    ):
        issues.append(_pre["node_too_old"].format(min_version=min_ver))
    for check in _pre["checks"][1:]:
        if not shutil.which(check["cmd"]):
            issues.append(check["missing"])
    return issues


def _cmd_auth(sessions_dir: Path) -> None:
    import shutil
    import subprocess

    if not shutil.which("node"):
        sys.exit("Error: Node.js not found")
    sessions_dir.mkdir(parents=True, exist_ok=True)
    bd = _BRIDGE.parent
    if (
        not (bd / "node_modules").exists()
        and subprocess.run(
            ["npm", "install", "--production", "--ignore-scripts", "--silent"], cwd=str(bd)
        ).returncode
        != 0
    ):
        sys.exit("npm install failed.")
    creds = sessions_dir / "creds.json"
    sz = creds.stat().st_size if creds.exists() else 0
    if sz > _cfg.creds_min_size:
        print("Already authenticated. Delete sessions/ to re-auth.")
        return
    if sz:
        shutil.rmtree(sessions_dir, ignore_errors=True)
        sessions_dir.mkdir(parents=True, exist_ok=True)
    print("Scan QR: WhatsApp > Linked Devices > Link a Device\n")
    proc = subprocess.Popen(
        ["node", str(_BRIDGE)],
        env={**os.environ, "SESSIONS_DIR": str(sessions_dir)},
        stderr=sys.stderr,
        stdout=subprocess.PIPE,
    )
    try:
        for raw in proc.stdout:  # type: ignore[union-attr]
            with contextlib.suppress(Exception):
                obj = json.loads(raw.decode().strip())
                if obj.get("event") == "ready":
                    print(
                        f"✅ Authenticated as {obj.get('adminJid', '').split(':')[0].split('@')[0]}"
                    )
                    break
    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait()


def _safe_app_json(name, desc=""):
    return {
        **_C["safe_app"],
        "name": name,
        "description": desc or name,
        "allowed_resources": [f"c3://memory/{name}/*"],
    }


def _ensure_safe_app_json(dest: Path, name: str, description: str = "") -> None:
    if (dest / FILE_APP_JSON).exists():
        return
    (dest / FILE_APP_JSON).write_text(json.dumps(_safe_app_json(name, description), indent=2))
    print("  ⚠️  Auto-generated app.json (host-only, sandboxed, minimal tools)")


def _scaffold_app(dest):
    n = dest.name
    files = {
        **{k.format(name=n): v.format(name=n) for k, v in _C["scaffold"].items()},
        FILE_APP_JSON: json.dumps(_safe_app_json(n, ""), indent=2),
    }
    for fn, c in files.items():
        target = dest / fn
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(c)
            print(f"wrote {fn}")


def _fetch_content(s: str) -> str:
    if s.startswith(("http://", "https://")):
        import urllib.request

        return urllib.request.urlopen(s, timeout=_cfg.fetch_timeout).read().decode()
    p = Path(s)
    return p.read_text() if s and p.is_file() else s


def _ensure_mcp_json(base: Path) -> Path:
    mf = base / ".mcp.json"
    sd = base / DIR_SESSIONS
    sd.mkdir(exist_ok=True)
    # Only write if missing or doesn't contain a whatsapp server
    existing = _read_json(mf)
    if not existing or "whatsapp" not in existing.get("mcpServers", {}):
        mf.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        **existing.get("mcpServers", {}),
                        "whatsapp": {
                            "command": "c3-py",
                            "args": [
                                "--serve",
                                "--agent-dir",
                                str(base),
                                "--sessions-dir",
                                str(sd),
                            ],
                        },
                    }
                },
                indent=2,
            )
        )
    return mf


def _run_claude_task(base: Path, prompt: str) -> None:
    import shutil
    import subprocess

    if not shutil.which("claude"):
        sys.exit("Error: 'claude' CLI required")
    subprocess.run(
        [
            "claude",
            "--model",
            _C["server"]["model"],
            "--mcp-config",
            str(_ensure_mcp_json(base)),
            "--agents",
            _build_apps_json(base),
            "--dangerously-skip-permissions",
            "-p",
            prompt,
        ],
        cwd=str(base),
    )


_app = typer.Typer(name="c3-py", help="c3 — WhatsApp AI agent framework", add_completion=False)


@_app.command("auth")
def _cli_auth(sessions_dir: str = typer.Option(DIR_SESSIONS, "--sessions-dir", "-s")):
    """Authenticate WhatsApp — scan QR code."""
    _cmd_auth(Path(sessions_dir))


@_app.command("check")
def _cli_check(agent_dir: str = typer.Option(".", "--agent-dir", "-d")):
    """Check prerequisites and validate app directory."""
    issues = _check_prereqs()
    [print(f"  ❌ {i}", file=sys.stderr) for i in issues] if issues else print(
        "  ✅ Prerequisites OK"
    )
    base = Path(agent_dir)
    for f, msg in _C["check_files"]:
        if not (base / f).exists():
            print(f"WARN  {f} missing — {msg}", file=sys.stderr)
    g = list((base / DIR_SKILLS).glob("*.md")) if (base / DIR_SKILLS).exists() else []
    if g:
        print(f"skills/: {len(g)} skill(s): {', '.join(x.stem for x in g)}")
    print("OK — app dir looks good")


_app_sub = typer.Typer(help="App management.")
_app.add_typer(_app_sub, name="app")


@_app_sub.command("new")
def _cli_app_new(
    name: str = typer.Argument(..., help="App name"),
    description: str = typer.Argument("", help="What the app does (Claude generates everything)"),
    agent_dir: str = typer.Option(".", "--agent-dir", "-d"),
):
    """Create a new app. Claude generates CLAUDE.md, app.json, and skills."""
    base = Path(agent_dir)
    dest = base / name
    if dest.exists():
        sys.exit(f"Error: app '{name}' already exists at {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / DIR_SKILLS).mkdir(exist_ok=True)
    _ensure_safe_app_json(dest, name, description)
    if description:
        _run_claude_task(base, _C["prompts"]["app_new"].format(name=name, description=description))
    else:
        _scaffold_app(dest)
        print(f'\n  Tip: c3-py app new {name} "description" to have Claude generate everything')


@_app_sub.command("list")
def _cli_app_list(agent_dir: str = typer.Option(".", "--agent-dir", "-d")):
    """List installed apps."""
    base = Path(agent_dir)
    bundled = _PKG / DIR_APPS
    seen = {
        d.name: ("bundled" if root == bundled else "local")
        for root in [bundled, base]
        if root.exists()
        for d in sorted(root.iterdir())
        if d.is_dir() and not d.name.startswith(".") and (d / FILE_CLAUDE_MD).exists()
    }
    if not seen:
        print("No apps found.")
        return
    for name, src in sorted(seen.items()):
        desc = _read_json(
            next(
                (
                    x
                    for x in [base / name / FILE_APP_JSON, bundled / name / FILE_APP_JSON]
                    if x.exists()
                ),
                base / name / FILE_APP_JSON,
            )
        ).get("description", "")
        print(f"  {name:20s} [{src}]  {desc}")


def _stage_and_review(base, app_name, content_type, source, content=None):
    dest = base / app_name
    P = _C["prompts"]
    R = _C["rules"]
    if content and content_type == "skill":
        (dest / DIR_SKILLS).mkdir(exist_ok=True)
        sn = Path(source).stem if Path(source).suffix == ".md" else app_name
        (dest / DIR_SKILLS / f"{sn}.md").write_text(content)
        _run_claude_task(base, P["review_skill"].format(app=app_name, file=f"{sn}.md", rules=R))
    elif content:
        (dest / FILE_CLAUDE_MD).write_text(content)
        _run_claude_task(base, P["review_prompt"].format(app=app_name, rules=R))
    elif content_type == "skill":
        _run_claude_task(base, P["gen_skill"].format(app=app_name, source=source, rules=R))
    else:
        _run_claude_task(base, P["gen_prompt"].format(app=app_name, source=source, rules=R))


@_app_sub.command("add")
def _cli_app_add(
    app_name: str = typer.Argument(..., help="Target app name"),
    content_type: str = typer.Argument(
        ..., help="What to add: skill, mcp, prompt, or a GitHub URL"
    ),
    source: str = typer.Argument("", help="URL, file path, description, or inline content"),
    agent_dir: str = typer.Option(".", "--agent-dir", "-d"),
):
    """Add content to an app."""
    base = Path(agent_dir)
    if content_type.startswith("http") or ("/" in content_type and not source):
        _cli_app_install_from_url(base, app_name, content_type)
        return
    dest = base / app_name
    dest.mkdir(parents=True, exist_ok=True)
    _ensure_safe_app_json(dest, app_name)
    if content_type == "mcp":
        if not source:
            sys.exit("Error: MCP config required")
        try:
            entry = json.loads(_fetch_content(source))
        except Exception as e:
            sys.exit(f"Error: {e}")
        mf = dest / "mcp.json"
        ex = _read_json(mf)
        sn = entry.get("name", Path(entry.get("command", "mcp")).stem)
        ex.setdefault("mcpServers", {})[sn] = entry
        mf.write_text(json.dumps(ex, indent=2))
        _run_claude_task(base, _C["prompts"]["mcp_added"].format(server=sn, app=app_name))
    elif content_type in ("skill", "prompt"):
        _stage_and_review(
            base,
            app_name,
            content_type,
            source,
            _fetch_content(source) if Path(source).exists() or source.startswith("http") else None,
        )
    else:
        sys.exit("Error: use skill, mcp, prompt, or a URL")


def _cli_app_install_from_url(base: Path, name: str, url: str) -> None:
    import shutil
    import subprocess
    import tempfile

    if "/" in url and not url.startswith("http"):
        url = f"https://github.com/{url}"
    dest = base / name
    if dest.exists():
        sys.exit(f"Error: '{name}' already exists")
    with tempfile.TemporaryDirectory() as tmp:
        if (
            subprocess.run(
                ["git", "clone", "--depth", "1", url, tmp], capture_output=True
            ).returncode
            != 0
        ):
            sys.exit("Error: clone failed")
        src = Path(tmp)
        src_dir = next(
            (
                d
                for d in [src, src / name, *(d for d in src.iterdir() if d.is_dir())]
                if (d / FILE_CLAUDE_MD).exists()
            ),
            src,
        )
        shutil.copytree(str(src_dir), str(dest), dirs_exist_ok=True)
    for g in [dest / ".git", dest / ".github"]:
        if g.exists():
            shutil.rmtree(g)
    mf = _read_json(dest / FILE_APP_JSON)
    if mf:
        mf.update(trust_level=TRUST_COMMUNITY, sandboxed=True)
        if "allowed_tools" in mf:
            mf["allowed_tools"] = [
                t for t in mf["allowed_tools"] if t not in {"save_file", "load_app"}
            ]
        (dest / FILE_APP_JSON).write_text(json.dumps(mf, indent=2))
    _ensure_safe_app_json(dest, name)
    print(f"✅ Installed '{name}'")


@_app_sub.command("install")
def _cli_app_install(
    url: str = typer.Argument(..., help="GitHub URL or shorthand (user/repo)"),
    agent_dir: str = typer.Option(".", "--agent-dir", "-d"),
):
    """Install a full app from GitHub. Shorthand for: app add <name> <url>"""
    name = url.rstrip("/").split("/")[-1].removeprefix("c3-").removeprefix("c3py-")
    _cli_app_install_from_url(Path(agent_dir), name, url)


@_app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    agent: Optional[str] = typer.Argument(None, help="App directory or name"),
    serve: bool = typer.Option(False, "--serve"),
    agent_dir: Optional[str] = typer.Option(None, "--agent-dir", "-d"),
    sessions_dir: Optional[str] = typer.Option(None, "--sessions-dir", "-s"),
    sse: bool = typer.Option(False, "--sse"),
    sse_url: Optional[str] = typer.Option(None, "--sse-url"),
    test: bool = typer.Option(False, "--test", help="Use test adapter instead of WhatsApp"),
    host: str = typer.Option(_cfg.host, "--host"),
    port: int = typer.Option(_cfg.port, "--port"),
) -> None:
    if ctx.invoked_subcommand:
        return
    adir = agent_dir
    if agent and not serve:
        p = Path(agent)
        adir = str(p.resolve()) if p.exists() else _find_app_dir(agent)
        if not adir:
            typer.echo(f"Error: app '{agent}' not found", err=True)
            raise typer.Exit(1)
        _launcher_mode(adir, False, sse_url=sse_url)
        return
    adir = adir or "."
    sdir = sessions_dir
    if not sdir and (Path(adir) / DIR_SESSIONS).exists():
        sdir = str(Path(adir) / DIR_SESSIONS)
    if test:
        from c3.test_adapter import TestAdapter

        wa: ChatAdapter = TestAdapter()
    else:
        wa = BaileysAdapter(sessions_dir=sdir)
    asyncio.run(
        create_channel(
            wa, agent_dir=adir, transport="sse" if sse else "stdio", host=host, port=port
        )
    )


def cli() -> None:
    _app()


if __name__ == "__main__":
    cli()
