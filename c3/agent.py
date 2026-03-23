"""c3-py — WhatsApp × Claude Code MCP server."""
from __future__ import annotations
import asyncio, contextlib, json, os, re, sys, uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import anyio, dataset as _dataset, typer
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, TextContent, Tool
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from watchfiles import awatch

class WAMessage(BaseModel):
    jid: str; sender: str; push_name: str; text: str
    timestamp: int; is_group: bool; message_id: str | None = None
class GroupMember(BaseModel):
    jid: str; name: str; is_admin: bool; lid: str | None = None
class HostConfig(BaseModel):
    jid: str; name: str; lid: str | None = None
class AppConfig(BaseSettings):
    # ── Hosts / access ────────────────────────────────────────────────────────
    hosts: list[HostConfig] = []; admins: list[HostConfig] = []
    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"          # C3_HOST
    port: int = 3000               # C3_PORT
    # ── Timeouts (seconds) ───────────────────────────────────────────────────
    bridge_connect_timeout: int = 120   # C3_BRIDGE_CONNECT_TIMEOUT
    baileys_cmd_timeout: int = 30       # C3_BAILEYS_CMD_TIMEOUT
    plugin_init_timeout: int = 10       # C3_PLUGIN_INIT_TIMEOUT
    test_message_timeout: int = 20      # C3_TEST_MESSAGE_TIMEOUT
    phase_expiry_grace: int = 3         # C3_PHASE_EXPIRY_GRACE   (sleep after timer fires)
    # ── Durations ────────────────────────────────────────────────────────────
    default_duration: int = 600         # C3_DEFAULT_DURATION     (parse_duration fallback)
    default_phase_timer: int = 60       # C3_DEFAULT_PHASE_TIMER  (set_phase_timer fallback)
    # ── Logging ──────────────────────────────────────────────────────────────
    log_truncate: int = 200             # C3_LOG_TRUNCATE
    model_config = SettingsConfigDict(env_prefix="C3_", extra="ignore")

_cfg = AppConfig()
class AccessPolicy(BaseModel):
    commands: dict[str, list[str]] = {}; dm: list[str] = []; group: list[str] = []
class PluginManifest(BaseModel):
    name: str; access: AccessPolicy
class ToolDef(BaseModel):
    name: str; description: str; input_schema: dict[str, Any]
class PluginSession:
    def grant(self, role: str, entries: list[dict]) -> None: ...
    def revoke(self, role: str) -> None: ...

class PluginMCPProxy:
    """Spawns a plugin's MCP subprocess and proxies its tools through c3-py."""
    def __init__(self, name: str, params: dict, plugin_dir: Path):
        self.name = name; self._params = params; self._plugin_dir = plugin_dir
        self._session: Any | None = None; self.tools: list[Tool] = []; self._ready = asyncio.Event()
    @property
    def tool_names(self) -> set[str]: return {t.name for t in self.tools}
    async def run(self) -> None:
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        env = {**os.environ, **{k: v.replace("${plugin_dir}", str(self._plugin_dir))
            for k, v in self._params.get("env", {}).items()}}
        params = StdioServerParameters(command=self._params["command"], args=self._params.get("args", []), env=env)
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize(); result = await session.list_tools()
                    self.tools = result.tools; self._session = session; self._ready.set()
                    log("aggregator", f"{self.name}: {len(self.tools)} tools loaded")
                    await anyio.sleep_forever()
        except Exception as e: log("aggregator", f"{self.name} error: {e}")
    async def call_tool(self, name: str, arguments: dict) -> list[TextContent]:
        await asyncio.wait_for(self._ready.wait(), timeout=_cfg.plugin_init_timeout)
        if not self._session:
            return [TextContent(type="text", text=f"Error: MCP server '{self.name}' not connected")]
        result = await self._session.call_tool(name, arguments)
        return result.content

class WAAdapter(ABC):
    admin_jid: str = ""
    on_message: Callable[[WAMessage], Awaitable[None]] | None = None
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
    @abstractmethod
    def get_name(self, jid: str) -> str: ...

_log_file: Path | None = None
def setup_logging(d):
    global _log_file; p = Path(d) / "logs"; p.mkdir(parents=True, exist_ok=True)
    _log_file = p / f"c3-{datetime.now().strftime('%Y-%m-%d')}.log"
def log(tag, msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] [{tag}] {msg}"
    print(line, file=sys.stderr, flush=True)
    if _log_file:
        with contextlib.suppress(Exception):
            with open(_log_file, "a") as f: f.write(line + "\n")

def parse_duration(value: Any, fallback: int | None = None) -> int:
    if fallback is None: fallback = _cfg.default_duration
    if value is None or value == "": return fallback
    if isinstance(value, (int, float)): return int(value)
    m = re.match(r'^(\d+)(s|m)?$', str(value).strip(), re.I)
    if not m: return int(str(value)) if str(value).isdigit() else fallback
    n = int(m.group(1)); return n * 60 if (m.group(2) or "").lower() == "m" else n

def pick(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None: return d[k]
    return None

class JidMask:
    def __init__(self):
        self._jid_to_token: dict[str, str] = {}; self._token_to_jid: dict[str, str] = {}
    def register(self, jid: str, token: str) -> None:
        if not jid or not token: return
        self._jid_to_token[jid] = token
        if token not in self._token_to_jid: self._token_to_jid[token] = jid
    def alias(self, jid: str, token: str) -> None:
        if jid and token: self._jid_to_token[jid] = token
    def mask(self, text: str) -> str:
        for jid, token in sorted(self._jid_to_token.items(), key=lambda x: len(x[0]), reverse=True):
            text = text.replace(jid, token)
        return text
    def unmask(self, token: str) -> str: return self._token_to_jid.get(token, token)
    def mask_meta(self, meta: dict) -> dict: return {k: self.mask(str(v)) for k, v in meta.items()}

class PluginController:
    def __init__(self, manifest: PluginManifest, config: AppConfig):
        self.jid_mask = JidMask(); self._manifest = manifest
        self._static_roles: dict[str, set[str]] = {}; self._dynamics: dict[str, set[str]] = {}
        def _reg(entries, tok_fn):
            jids: set[str] = set()
            for x in entries:
                jids.add(x.jid); tok = tok_fn(x)
                self.jid_mask.register(x.jid, tok)
                if x.lid: jids.add(x.lid); self.jid_mask.alias(x.lid, tok)
            return jids
        self._static_roles["hosts"] = _reg(config.hosts, lambda _: "host")
        self._static_roles["admins"] = _reg(config.admins,
            lambda a: "host" if any(h.jid == a.jid for h in config.hosts) else "admin")
        log("policy", f"loaded: {manifest.name}")
    def can_reach(self, jid: str, is_group: bool, text: str) -> bool:
        access = self._manifest.access
        if not is_group:
            if text.strip().startswith("/"):
                cmd = text.strip().split()[0].lower()
                return any(self._has_role(jid, r) for r in access.commands.get(cmd, []))
            return any(self._has_role(jid, r) for r in access.dm)
        return any(self._has_role(jid, r) for r in access.group)
    def create_session(self) -> PluginSession:
        ctrl = self
        class _Session(PluginSession):
            def grant(self, role: str, entries: list[dict]) -> None:
                jids: set[str] = set()
                for e in entries: ctrl.jid_mask.register(e["jid"], e["token"]); jids.add(e["jid"])
                ctrl._dynamics[role] = jids; log("policy", f"grant({role}): {len(jids)}")
            def revoke(self, role: str) -> None:
                ctrl._dynamics.pop(role, None); log("policy", f"revoke({role})")
        return _Session()
    def _has_role(self, jid: str, role: str) -> bool:
        return jid in self._static_roles.get(role, set()) or jid in self._dynamics.get(role, set())

class SessionEngine:
    def __init__(self, wa: WAAdapter, notify: Callable, ctrl: PluginController, plugin_dir: str | Path = "."):
        self._wa = wa; self._notify = notify; self._sessions_dir = Path(plugin_dir) / "sessions"
        self._ctrl = ctrl; self._phase_timers: dict[str, asyncio.TimerHandle] = {}
        self._poll_listeners: dict[str, Callable] = {}; self._poll_tallies: dict[str, dict] = {}
        self._stop_poll_map: dict[str, str] = {}
        self._active_sessions: dict[str, str] = {}  # group_jid → session_name
        self._loaded_plugins: set[str] = set()
        orig = wa.on_poll_update
        async def _poll_handler(poll_id: str, tally: dict) -> None:
            await self._handle_stop_poll(poll_id, tally); await self._dispatch_poll(poll_id, tally)
            if orig: await orig(poll_id, tally)
        wa.on_poll_update = _poll_handler
    def resolve_group(self, token: str | None) -> str | None:
        if token and token != "group": return self._ctrl.jid_mask.unmask(token)
        sessions = list(self._active_sessions.keys())
        return sessions[0] if sessions else None
    def set_active(self, group_jid: str, name: str) -> None:
        self._active_sessions[group_jid] = name; log("engine", f"active: {name} ({len(self._active_sessions)} sessions)")
    def clear_active(self, group_jid: str | None = None) -> None:
        if group_jid: self._active_sessions.pop(group_jid, None)
        else: self._active_sessions.clear()
        log("engine", f"cleared (remaining: {len(self._active_sessions)})")
    def track_poll(self, poll_id: str, group_jid: str, question: str) -> None:
        self._poll_tallies[poll_id] = {}  # accumulate votes
        async def listener(pid: str, tally: dict) -> None:
            if pid != poll_id: return
            self._poll_tallies[poll_id] = tally  # update latest tally
            lines = [f'POLL UPDATE — "{question}"']
            for opt, voters in tally.items(): lines.append(f"  {opt}: {len(voters)} votes")
            await self._notify("\n".join(lines), {"type": "poll_update", "poll_id": poll_id, "group_jid": group_jid})
        self._poll_listeners[poll_id] = listener

    def get_poll_tally(self, poll_id: str) -> dict:
        """Get the full tally with voter names — only call after timer expires."""
        return self._poll_tallies.pop(poll_id, {})
    async def _dispatch_poll(self, poll_id: str, tally: dict) -> None:
        for listener in list(self._poll_listeners.values()):
            try: await listener(poll_id, tally)
            except Exception as e: log("engine", f"poll listener error: {e}")
    def set_phase_timer(self, group_jid: str, seconds: int, phase_name: str) -> None:
        self._cancel_timer(group_jid); loop = asyncio.get_running_loop()
        async def _fire() -> None:
            await asyncio.sleep(_cfg.phase_expiry_grace)
            # Include final tallies for any active polls so Claude has real voter data
            tally_lines: list[str] = []
            for pid, tally in list(self._poll_tallies.items()):
                for opt, voters in tally.items():
                    tally_lines.append(f"  {opt}: {len(voters)} — {', '.join(voters)}")
                self._poll_tallies.pop(pid, None)
                self._poll_listeners.pop(pid, None)
            tally_str = "\nFINAL VOTES:\n" + "\n".join(tally_lines) if tally_lines else "\nNo votes received."
            await self._notify(f"PHASE EXPIRED: {phase_name}\nTime is up. Resolve and advance.{tally_str}",
                {"type": "phase_expired", "group_jid": group_jid, "phase": phase_name})
        self._phase_timers[group_jid] = loop.call_later(seconds, lambda: asyncio.ensure_future(_fire()))
        log("engine", f"timer: {phase_name} ({seconds}s)")
    def clear_all_timers(self) -> None:
        for handle in self._phase_timers.values(): handle.cancel()
        self._phase_timers.clear(); self._poll_listeners.clear()
    def _cancel_timer(self, group_jid: str) -> None:
        h = self._phase_timers.pop(group_jid, None)
        if h: h.cancel()
    def _plugin_dirs(self) -> list[Path]:
        base = self._sessions_dir.parent; bundled = Path(__file__).parent / "plugins"
        skill_dirs = []
        for root in [base, bundled]:
            if not root.exists(): continue
            for d in sorted(root.iterdir()):
                if d.is_dir() and not d.name.startswith("."):
                    sd = d / "skills"
                    if sd.is_dir(): skill_dirs.append(sd)
        return skill_dirs
    def _list_plugins(self) -> list[str]:
        return sorted({f.stem for d in self._plugin_dirs() for f in d.glob("*.md")})
    def _load_plugin_content(self, name: str) -> str | None:
        for d in self._plugin_dirs():
            p = d / f"{name}.md"
            if p.exists(): return p.read_text()
        return None
    async def handle(self, msg: WAMessage) -> bool:
        if msg.is_group or not msg.text.startswith("/"): return False
        parts = msg.text.strip().split(); cmd = parts[0].lower()
        if cmd == "/start":
            stop_id = await self._wa.send_poll(msg.sender, "🛑 Stop the session anytime", ["Stop now"])
            self._stop_poll_map[msg.sender] = stop_id
            args = " ".join(parts[1:]) if len(parts) > 1 else ""
            meta: dict = {"type": "setup_start", "host_jid": "host"}
            if args: meta["args"] = args
            await self._notify(f"HOST WANTS TO START\nHost: (host)\nArgs: {args or '(none)'}", meta); return True
        if cmd == "/stop":
            self.clear_all_timers(); self.clear_active()
            await self._notify("HOST COMMAND: STOP\nStopped by (host).", {"type": "session_stop", "host_jid": "host"})
            await self._wa.send(msg.sender, "✅ Stop signal sent."); return True
        if cmd == "/status":
            sessions = len(self._active_sessions); timers = len(self._phase_timers)
            await self._wa.send(msg.sender, f"📊 Sessions: {sessions} | Timers: {timers}"); return True
        if cmd == "/plugin":
            sub = parts[1].lower() if len(parts) > 1 else "list"
            name = parts[2].lower().removesuffix(".md") if len(parts) > 2 else ""
            if sub == "list":
                await self._wa.send(msg.sender, "📦 Available: " + (", ".join(self._list_plugins()) or "none") + "\n✅ Loaded: " + (", ".join(sorted(self._loaded_plugins)) or "none")); return True
            if sub == "add" and name:
                content = self._load_plugin_content(name)
                if not content: await self._wa.send(msg.sender, f"❌ Plugin '{name}' not found. Try /plugin list"); return True
                self._loaded_plugins.add(name); await self._notify(content, {"type": "skill_load", "skill": name})
                await self._wa.send(msg.sender, f"✅ Plugin '{name}' loaded"); return True
            if sub == "remove" and name:
                self._loaded_plugins.discard(name)
                await self._notify(f"Plugin '{name}' has been unloaded. Stop using its behaviors.", {"type": "skill_unload", "skill": name})
                await self._wa.send(msg.sender, f"✅ Plugin '{name}' unloaded"); return True
            await self._wa.send(msg.sender, "Usage: /plugin list | /plugin add <name> | /plugin remove <name>"); return True
        return False
    async def _handle_stop_poll(self, poll_id: str, tally: dict) -> None:
        for host_jid, stop_id in list(self._stop_poll_map.items()):
            if poll_id != stop_id: continue
            # Only the host who created this stop poll can trigger it
            for _opt, voters in tally.items():
                if not any(host_jid in v or host_jid.split(":")[0] in v for v in [voters]): continue
                del self._stop_poll_map[host_jid]; self.clear_all_timers(); self.clear_active()
                await self._notify("HOST COMMAND: STOP (poll)", {"type": "session_stop", "host_jid": "host"})
                await self._wa.send(host_jid, "✅ Stop signal sent."); return

def _T(n, d, p, r=None): return ToolDef(name=n, description=d, input_schema={"type": "object", "properties": p, **({"required": r} if r else {})})
_s = lambda d="": {"type": "string", "description": d} if d else {"type": "string"}
_n = {"type": "number"}; _obj = {"type": "object"}
_arr = {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 12}

BASE_TOOLS: list[ToolDef] = [
    # ── Messaging ────────────────────────────────────────────────────────────
    _T("reply", "Send a message to a user or group.", {"jid": _s("Token: 'host', 'group', or a name"), "text": _s()}, ["jid", "text"]),
    _T("send_private", "Send a private DM.", {"jid": _s(), "text": _s()}, ["jid", "text"]),
    _T("send_poll", "Send a single-choice WhatsApp poll.",
        {"group_jid": _s(), "question": _s(), "options": _arr}, ["group_jid", "question", "options"]),
    # ── Groups ───────────────────────────────────────────────────────────────
    _T("get_group_members", "Get the member list of a WhatsApp group.", {"group_jid": _s()}, ["group_jid"]),
    _T("resolve_group", "Resolve a WhatsApp invite link to a group JID and register its members.",
        {"invite_link": _s("https://chat.whatsapp.com/...")}, ["invite_link"]),
    # ── Session ──────────────────────────────────────────────────────────────
    _T("set_timer", "Start a named countdown. Fires a timer_expired notification when done.",
        {"seconds": _n, "name": _s("Timer label"), "group_jid": _s("Optional group context")}, ["seconds", "name"]),
    _T("end_session", "End the active session — clears timers and revokes participant access.",
        {"group_jid": _s("Optional — clears specific group, or all if omitted")}),
    # ── Memory ───────────────────────────────────────────────────────────────
    _T("memory_write", "Store an entity in persistent memory.",
        {"entity": {"type": "object", "description": "Must include 'plugin' and 'entity' fields"}}, ["entity"]),
    _T("memory_read", "Read entities from memory, optionally filtered.",
        {"plugin": _s("Filter by plugin"), "entity_type": _s("Filter by entity type")}),
    _T("memory_search", "Full-text search across all memory.", {"query": _s()}, ["query"]),
    _T("memory_delete", "Delete matching entities from memory.",
        {"plugin": _s(), "entity_type": _s(), "name": _s()}),
    # ── Plugins ──────────────────────────────────────────────────────────────
    _T("load_plugin", "Load a plugin's skills and MCP tools on demand.",
        {"name": _s("Plugin name or skill name")}, ["name"]),
    # ── Files ────────────────────────────────────────────────────────────────
    _T("save_file", "Write content to a file in the data directory.",
        {"path": _s("Relative path e.g. 'summaries/recap.md'"), "content": _s()}, ["path", "content"]),
]

def find_plugin_content(name: str, base: Path) -> list[tuple[str, str]]:
    """Return [(skill_name, content)] for a plugin dir or individual skill under base or bundled."""
    bundled = Path(__file__).parent / "plugins"
    for root in [base, bundled]:
        path = root / name
        if path.is_dir():
            out: list[tuple[str, str]] = []
            claude = path / "CLAUDE.md"
            if claude.exists():
                try: out.append((f"{name}/CLAUDE.md", claude.read_text()))
                except OSError as e: log("plugin", f"failed to read {claude}: {e}")
            skills_dir = path / "skills"
            if skills_dir.is_dir():
                for f in sorted(skills_dir.glob("*.md")):
                    try: out.append((f.stem, f.read_text()))
                    except OSError as e: log("plugin", f"failed to read {f}: {e}")
            return out
        for plugin_dir in sorted(root.iterdir()) if root.exists() else []:
            if plugin_dir.is_dir() and not plugin_dir.name.startswith("."):
                skill = plugin_dir / "skills" / f"{name}.md"
                if skill.exists():
                    try: return [(name, skill.read_text())]
                    except OSError as e: log("plugin", f"failed to read {skill}: {e}")
    return []

_mem_cache: dict[str, Any] = {}
def _mem(base: Path):
    key = str(base)
    if key not in _mem_cache:
        _mem_cache[key] = _dataset.connect(f"sqlite:///{base}/memory.db", row_type=dict)
    return _mem_cache[key]

class ChannelCore:
    def __init__(self, wa, ctrl, session, engine, notify, plugin_dir=".", plugin_proxies=None, notify_queue=None):
        self._wa = wa; self._ctrl = ctrl; self._session = session; self._engine = engine
        self._notify = notify; self._base = Path(plugin_dir)
        self._plugin_proxies = plugin_proxies or {}; self._notify_queue = notify_queue
    async def call_tool(self, name: str, arguments: dict) -> list[TextContent]:
        a = arguments or {}; log("tool", f"{name} {json.dumps(a)[:_cfg.log_truncate]}")
        # ── Messaging ────────────────────────────────────────────────────────
        if name in ("reply", "send_private"):
            jid = self._ctrl.jid_mask.unmask(pick(a, "jid", "to", "recipient") or "host")
            text = pick(a, "text", "message", "content") or ""; await self._wa.send(jid, text)
            return [TextContent(type="text", text="sent")]
        if name == "send_poll":
            options = a.get("options", [])
            if isinstance(options, str):
                try: options = json.loads(options)
                except (ValueError, json.JSONDecodeError): options = []
            if len(options) < 2: return [TextContent(type="text", text="Error: options must be array ≥2")]
            group_jid = self._ctrl.jid_mask.unmask(pick(a, "group_jid", "jid", "to") or "group")
            if not group_jid.endswith("@g.us"): return [TextContent(type="text", text="Error: group not resolved")]
            poll_id = await self._wa.send_poll(group_jid, a["question"], options)
            self._engine.track_poll(poll_id, group_jid, a["question"])
            return [TextContent(type="text", text=f"poll: {poll_id}")]
        # ── Groups ───────────────────────────────────────────────────────────
        if name == "get_group_members":
            group_jid = self._ctrl.jid_mask.unmask(pick(a, "group_jid", "jid") or "group")
            if not group_jid.endswith("@g.us"): return [TextContent(type="text", text="Error: group not resolved")]
            members = await self._wa.get_group_members(group_jid)
            return [TextContent(type="text", text="\n".join(f'{m.name}{" (admin)" if m.is_admin else ""}' for m in members) or "No members")]
        if name == "resolve_group":
            link = pick(a, "invite_link", "link") or ""
            if not link or "chat.whatsapp.com" not in link:
                return [TextContent(type="text", text="Error: provide a valid WhatsApp invite link")]
            try: group_jid = await self._wa.resolve_group(link)
            except Exception as e: return [TextContent(type="text", text=f"Error: {e}")]
            members = await self._wa.get_group_members(group_jid)
            self._session.grant("session_group", [{"jid": group_jid, "token": "group"}])
            if members:
                entries = [{"jid": m.jid, "token": m.name} for m in members]
                for m in members:
                    if m.lid: entries.append({"jid": m.lid, "token": m.name})
                self._session.grant("session_participants", entries)
            self._engine.set_active(group_jid, "session")
            log("c3", f"resolved group: {group_jid} ({len(members)} members)")
            member_list = "\n".join(f'{m.name}{" (admin)" if m.is_admin else ""}' for m in members) or "No members"
            return [TextContent(type="text", text=f"GROUP: group\nMEMBERS ({len(members)}):\n{member_list}")]
        # ── Session ──────────────────────────────────────────────────────────
        if name == "set_timer":
            seconds = parse_duration(pick(a, "seconds", "duration", "time"), _cfg.default_phase_timer)
            timer_name = pick(a, "name", "phase_name", "phase") or "timer"
            group_jid = self._engine.resolve_group(pick(a, "group_jid", "jid", "group"))
            if not group_jid: return [TextContent(type="text", text="Error: no active session")]
            self._engine.set_phase_timer(group_jid, seconds, timer_name)
            return [TextContent(type="text", text=f"timer: {timer_name} ({seconds}s)")]
        if name == "end_session":
            group_jid = pick(a, "group_jid", "jid")
            if group_jid: group_jid = self._ctrl.jid_mask.unmask(group_jid)
            self._engine.clear_all_timers(); self._engine.clear_active(group_jid)
            self._session.revoke("session_participants"); self._session.revoke("session_group")
            return [TextContent(type="text", text="session ended")]
        # ── Memory ───────────────────────────────────────────────────────────
        if name == "memory_write":
            entity = a.get("entity") or {}
            if not entity: return [TextContent(type="text", text="Error: entity required")]
            if "plugin" not in entity or "entity" not in entity:
                return [TextContent(type="text", text="Error: entity must include 'plugin' and 'entity' fields")]
            pk = [k for k in ("plugin", "entity", "name") if k in entity]
            _mem(self._base)["entities"].upsert(entity, pk)
            return [TextContent(type="text", text="ok")]
        if name == "memory_read":
            kwargs = {k: a[k] for k in ("plugin", "entity_type") if a.get(k)}
            if "entity_type" in kwargs: kwargs["entity"] = kwargs.pop("entity_type")
            rows = list(_mem(self._base)["entities"].find(**kwargs))
            return [TextContent(type="text", text=json.dumps(rows, indent=2))]
        if name == "memory_search":
            q = (a.get("query") or "").strip()
            if not q: return [TextContent(type="text", text="[]")]
            db = _mem(self._base)
            try:
                table = db["entities"]
                cols = [c for c in table.columns if c != "id"]
                where = " OR ".join(f'CAST("{c}" AS TEXT) LIKE :q' for c in cols)
                rows = list(db.query(f"SELECT * FROM entities WHERE {where}", q=f"%{q}%"))
            except Exception:
                rows = [r for r in db["entities"].all() if q.lower() in json.dumps(r).lower()]
            return [TextContent(type="text", text=json.dumps(rows, indent=2))]
        if name == "memory_delete":
            kwargs = {k: a[k] for k in ("plugin", "entity_type", "name") if a.get(k)}
            if "entity_type" in kwargs: kwargs["entity"] = kwargs.pop("entity_type")
            _mem(self._base)["entities"].delete(**kwargs)
            return [TextContent(type="text", text="ok")]
        # ── Plugins ──────────────────────────────────────────────────────────
        if name == "load_plugin":
            pname = (a.get("name") or "").strip().lower().removesuffix(".md")
            if not pname: return [TextContent(type="text", text="Error: name required")]
            skills = find_plugin_content(pname, self._base)
            if not skills: return [TextContent(type="text", text=f"Error: plugin '{pname}' not found")]
            msgs: list[str] = []
            for skill_name, content in skills: await self._notify(content, {"type": "skill_load", "skill": skill_name})
            msgs.append(f"Skills loaded: {', '.join(s for s, _ in skills)}")
            if pname not in self._plugin_proxies:
                for root in [self._base, Path(__file__).parent / "plugins"]:
                    mcp_file = root / pname / ".mcp"
                    if mcp_file.exists():
                        try:
                            params = json.loads(mcp_file.read_text())
                            proxy = PluginMCPProxy(pname, params, self._base)
                            self._plugin_proxies[pname] = proxy; asyncio.ensure_future(proxy.run())
                            try:
                                await asyncio.wait_for(proxy._ready.wait(), timeout=_cfg.plugin_init_timeout)
                                msgs.append(f"MCP server: {len(proxy.tools)} tools added")
                            except asyncio.TimeoutError: msgs.append("MCP server starting...")
                            if self._notify_queue: await self._notify_queue.put(("", {"_tools_changed": True}))
                        except Exception as e: log("plugin", f"failed to load MCP for '{pname}': {e}")
                        break
            return [TextContent(type="text", text="\n".join(msgs))]
        # ── Files ────────────────────────────────────────────────────────────
        if name == "save_file":
            rel_path = pick(a, "path", "filename") or ""
            if not rel_path: return [TextContent(type="text", text="Error: path required")]
            target = (self._base / rel_path).resolve()
            if not str(target).startswith(str(self._base.resolve())):
                return [TextContent(type="text", text="Error: path must be within plugin directory")]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(a.get("content", ""))
            return [TextContent(type="text", text=f"saved: {rel_path}")]
        raise ValueError(f"Unknown tool: {name}")
    async def on_message(self, msg: WAMessage) -> None:
        log("msg", f"from={msg.push_name} jid={msg.sender} group={msg.is_group} text={msg.text[:80]}")
        if not self._ctrl.can_reach(msg.sender, msg.is_group, msg.text):
            # Auto-admit: if message is from a group with an active session, grant the sender
            if msg.is_group and self._ctrl._has_role(msg.jid, "session_group"):
                name = msg.push_name or msg.sender.split("@")[0]
                self._ctrl._dynamics.setdefault("session_participants", set()).add(msg.sender)
                self._ctrl.jid_mask.register(msg.sender, name)
                log("policy", f"auto-admitted: {name} ({msg.sender})")
            else:
                log("policy", f"dropped: {msg.sender}"); return
        if await self._engine.handle(msg): return
        # Sanitize: strip XML-like tags and prompt format markers that could blend with MCP envelope
        sanitized = re.sub(r'</?(?:channel|system|human|assistant|tool)[^>]*>', '', msg.text)
        sanitized = re.sub(r'\n\n(Human|Assistant|System):', r'\n\n[\1]:', sanitized)
        await self._notify(self._ctrl.jid_mask.mask(sanitized),
            {"type": "message", "jid": self._ctrl.jid_mask.mask(msg.jid),
             "sender": self._ctrl.jid_mask.mask(msg.sender), "name": msg.push_name or "",
             **( {"group": "true"} if msg.is_group else {})})

_DEFAULT_MANIFEST = PluginManifest(name="c3", access=AccessPolicy(
    commands={"/start": ["hosts", "admins"], "/stop": ["hosts", "admins"], "/status": ["hosts", "admins"], "/plugin": ["hosts", "admins"]},
    dm=["hosts", "admins"], group=[]))

def _merge_manifests(extras: list[dict]) -> PluginManifest:
    dm: set[str] = set(); group: set[str] = set(); commands: dict[str, set[str]] = {}
    for m in [_DEFAULT_MANIFEST, *extras]:
        access = m.access if isinstance(m, PluginManifest) else AccessPolicy(**{
            "commands": m.get("access", {}).get("commands", {}),
            "dm": m.get("access", {}).get("dm", []), "group": m.get("access", {}).get("group", [])})
        dm.update(access.dm); group.update(access.group)
        for cmd, roles in access.commands.items(): commands.setdefault(cmd, set()).update(roles)
    names = [(m.name if isinstance(m, PluginManifest) else m.get("name", "")) for m in extras]
    return PluginManifest(name="+".join(n for n in names if n) or "c3",
        access=AccessPolicy(dm=list(dm), group=list(group), commands={k: list(v) for k, v in commands.items()}))

_BRIDGE = Path(__file__).parent / "baileys_bridge.js"

class BaileysAdapter(WAAdapter):
    def __init__(self, bridge: str | None = None, sessions_dir: str | None = None):
        self._bridge = bridge or str(_BRIDGE); self._sessions = sessions_dir
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}; self._bg_tasks: set[asyncio.Task] = set()
        self._next_id = 1; self.admin_jid = ""
    async def connect(self) -> None:
        bridge_dir = Path(self._bridge).parent; await self._ensure_node_modules(bridge_dir)
        env = {**os.environ}
        if self._sessions: env["SESSIONS_DIR"] = self._sessions
        self._proc = await asyncio.create_subprocess_exec("node", self._bridge,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=sys.stderr, env=env)
        log("baileys", f"bridge started (pid={self._proc.pid})")
        self._read_task = asyncio.ensure_future(self._read_events())
        ready = asyncio.get_running_loop().create_future(); orig_ready = self.on_ready
        async def _on_ready() -> None:
            if not ready.done(): ready.set_result(None)
            if orig_ready: await orig_ready()
        self.on_ready = _on_ready; await asyncio.wait_for(ready, timeout=_cfg.bridge_connect_timeout)
    async def send(self, jid: str, text: str) -> None: await self._cmd(cmd="send", jid=jid, text=text)
    async def send_poll(self, jid: str, question: str, options: list[str]) -> str:
        return str(await self._cmd(cmd="sendPoll", jid=jid, question=question, options=options))
    async def resolve_group(self, invite_link: str) -> str:
        return str(await self._cmd(cmd="resolveGroup", link=invite_link))
    async def get_group_members(self, group_jid: str) -> list[GroupMember]:
        result = await self._cmd(cmd="getGroupMembers", groupJid=group_jid)
        return [GroupMember(jid=m["jid"], name=m["name"], is_admin=m["isAdmin"], lid=m.get("lid")) for m in (result or [])]
    def get_name(self, jid: str) -> str: return jid.split("@")[0]
    @staticmethod
    async def _ensure_node_modules(bridge_dir: Path) -> None:
        install_dir = bridge_dir
        for d in [bridge_dir, *bridge_dir.parents]:
            if (d / "package.json").exists(): install_dir = d; break
        if (install_dir / "node_modules").exists() or not (install_dir / "package.json").exists(): return
        log("baileys", f"installing node deps in {install_dir} (first run)...")
        proc = await asyncio.create_subprocess_exec("npm", "install", "--production", "--ignore-scripts", "--silent",
            cwd=str(install_dir), stderr=sys.stderr)
        if await proc.wait() != 0: raise RuntimeError("npm install failed — is Node.js installed?")
        log("baileys", "node dependencies installed")
    async def _cmd(self, **kwargs) -> object:
        if not self._proc or not self._proc.stdin: raise RuntimeError("bridge not connected")
        cmd_id = self._next_id; self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[cmd_id] = fut
        payload = json.dumps({"id": cmd_id, **kwargs}) + "\n"
        self._proc.stdin.write(payload.encode()); await self._proc.stdin.drain()
        return await asyncio.wait_for(fut, timeout=_cfg.baileys_cmd_timeout)
    async def _read_events(self) -> None:
        assert self._proc and self._proc.stdout
        async for raw_line in self._proc.stdout:
            line = raw_line.decode().strip()
            if not line: continue
            try: obj = json.loads(line)
            except json.JSONDecodeError: log("baileys", f"bad JSON: {line[:_cfg.log_truncate]}"); continue
            if "id" in obj:
                fut = self._pending.pop(obj["id"], None)
                if fut and not fut.done():
                    if "error" in obj: fut.set_exception(RuntimeError(obj["error"]))
                    else: fut.set_result(obj.get("result"))
                continue
            event = obj.get("event")
            if event == "ready":
                self.admin_jid = obj.get("adminJid", ""); log("baileys", f"ready, admin={self.admin_jid}")
                if self.on_ready: await self.on_ready()
            elif event == "message":
                raw = obj.get("msg")
                if not raw: continue
                msg = WAMessage(jid=raw["jid"], sender=raw["sender"],
                    push_name=raw.get("pushName") or raw["sender"].split("@")[0],
                    text=raw["text"], timestamp=raw.get("timestamp", 0),
                    is_group=raw["isGroup"], message_id=raw.get("messageId"))
                if self.on_message:
                    task = asyncio.ensure_future(self.on_message(msg))
                    self._bg_tasks.add(task); task.add_done_callback(self._bg_tasks.discard)
            elif event == "poll_update":
                if self.on_poll_update:
                    task = asyncio.ensure_future(self.on_poll_update(obj["pollId"], obj["tally"]))
                    self._bg_tasks.add(task); task.add_done_callback(self._bg_tasks.discard)

async def create_channel(wa: WAAdapter, plugin_dir: str = ".", transport: str = "stdio", host: str = "0.0.0.0", port: int = 3000) -> None:
    base = Path(plugin_dir); bundled = Path(__file__).parent / "plugins"; setup_logging(base)
    session_id = str(uuid.uuid4()); started_at = datetime.now(timezone.utc).isoformat()
    sessions_dir = base / "sessions"; sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "current.json").write_text(json.dumps({
        "session_id": session_id, "started_at": started_at, "plugin_dir": str(base.resolve())}, indent=2))
    log("c3", f"session {session_id} started")
    def _read(rel: str) -> str | None:
        return next((p.read_text() for d in [base, bundled] for p in [d / rel] if p.exists()), None)
    try:
        raw = json.loads((base / "config.json").read_text())
        config = AppConfig(hosts=[HostConfig(**h) for h in raw.get("hosts", [])],
                           admins=[HostConfig(**a) for a in raw.get("admins", [])])
    except Exception:
        log("c3", "no config.json — no hosts configured"); config = AppConfig()
    parts: list[str] = []; claude_md = _read("CLAUDE.md")
    if claude_md: parts.append(claude_md)
    # Load memory schemas from plugins (but NOT their CLAUDE.md — those go to subagents via --agents)
    memory_schemas: dict = {}; seen_cats: set[str] = set()
    for search_root in [base, bundled]:
        if not search_root.exists(): continue
        for cat_dir in sorted(search_root.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith(".") or cat_dir.name in seen_cats: continue
            seen_cats.add(cat_dir.name)
            if (cat_dir / ".memory_schema").exists():
                try: memory_schemas.update(json.loads((cat_dir / ".memory_schema").read_text()))
                except (OSError, json.JSONDecodeError) as e: log("plugin", f"bad .memory_schema in {cat_dir.name}: {e}")
    if memory_schemas: parts.append(f"## Memory Schema\n\n```json\n{json.dumps(memory_schemas, indent=2)}\n```")
    instructions = "\n\n---\n\n".join(parts); _plugin_proxies: dict[str, PluginMCPProxy] = {}
    def _scan_mcp_files() -> dict[str, dict]:
        found: dict[str, dict] = {}
        for sr in [base, bundled]:
            if not sr.exists(): continue
            for cat in sorted(sr.iterdir()):
                if cat.is_dir() and not cat.name.startswith(".") and cat.name not in found and (cat / ".mcp").exists():
                    try: found[cat.name] = json.loads((cat / ".mcp").read_text())
                    except Exception as e: log("plugin", f"bad .mcp in {cat.name}: {e}")
        return found
    raw_manifests: list[dict] = []; seen_pj: set[str] = set()
    for _pj_root in [base, bundled]:
        for _pj_path in [_pj_root / "plugin.json"] + sorted(
                [d / "plugin.json" for d in _pj_root.iterdir() if d.is_dir() and not d.name.startswith(".")] if _pj_root.exists() else []):
            if _pj_path.exists() and str(_pj_path) not in seen_pj:
                seen_pj.add(str(_pj_path))
                try: raw_manifests.append(json.loads(_pj_path.read_text()))
                except (ValueError, json.JSONDecodeError) as e: log("plugin", f"bad plugin.json at {_pj_path}: {e}")
    manifest = _merge_manifests(raw_manifests); ctrl = PluginController(manifest, config)
    notify_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=500)
    async def notify(content: str, meta: dict) -> None: await notify_queue.put((content, meta))
    session = ctrl.create_session(); engine = SessionEngine(wa, notify, ctrl, plugin_dir=base)
    core = ChannelCore(wa, ctrl, session, engine, notify, base, _plugin_proxies, notify_queue)
    server = Server("c3", instructions=instructions or None)
    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        tools = [Tool(name=t.name, description=t.description, inputSchema=t.input_schema) for t in BASE_TOOLS]
        for proxy in _plugin_proxies.values(): tools.extend(proxy.tools)
        return tools
    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[TextContent]:
        for proxy in _plugin_proxies.values():
            if name in proxy.tool_names: return await proxy.call_tool(name, arguments)
        return await core.call_tool(name, arguments)
    wa.on_message = core.on_message
    async def _on_wa_ready() -> None:
        await notify(f"SESSION START\nsession_id: {session_id}\nstarted_at: {started_at}",
            {"type": "session_start", "session_id": session_id, "started_at": started_at})
    wa.on_ready = _on_wa_ready
    async def _drain_notifications(write_stream) -> None:
        while True:
            content, meta = await notify_queue.get()
            if meta.get("_tools_changed"):
                with contextlib.suppress(Exception):
                    notif = JSONRPCNotification(jsonrpc="2.0", method="notifications/tools/list_changed", params={})
                    await write_stream.send(SessionMessage(JSONRPCMessage(root=notif)))
                continue
            mc = ctrl.jid_mask.mask(content); mm = ctrl.jid_mask.mask_meta(meta) if meta else meta
            log("notify", f"type={mm.get('type', '?')} {mc[:_cfg.log_truncate].replace(chr(10), ' ')}")
            try:
                notif = JSONRPCNotification(jsonrpc="2.0", method="notifications/claude/channel",
                    params={"content": mc, "meta": mm})
                await write_stream.send(SessionMessage(JSONRPCMessage(root=notif)))
            except Exception as e: log("error", f"notify failed: {e}")
    def _boot_proxies(tg) -> None:
        for pname, pparams in _scan_mcp_files().items():
            proxy = PluginMCPProxy(pname, pparams, base); _plugin_proxies[pname] = proxy; tg.start_soon(proxy.run)
    _scheduler = AsyncIOScheduler()
    for root in [base, bundled]:
        if not root.exists(): continue
        for cat in [c for c in root.iterdir() if c.is_dir() and (c / ".crons").exists()]:
            try:
                for job in json.loads((cat / ".crons").read_text()):
                    async def _fire(j=job, p=cat.name): await notify(json.dumps({"job": j["job"], "plugin": p}), {"type": "cron_tick"})
                    _scheduler.add_job(_fire, CronTrigger.from_crontab(job["schedule"]), id=f"{cat.name}:{job['job']}", replace_existing=True)
            except Exception as e: log("cron", f"bad .crons in {cat.name}: {e}")
    _scheduler.start()
    import signal
    def _shutdown(sig, frame):
        log("c3", f"shutting down (signal {sig})...")
        _scheduler.shutdown(wait=False)
        if hasattr(wa, '_proc') and wa._proc: wa._proc.terminate()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    async def _watch_plugins() -> None:
        dirs = [str(d) for d in [base, bundled] if d.exists()]
        if not dirs: return
        async for changes in awatch(*dirs):
            for _, path in changes:
                if not path.endswith(".md"): continue
                with contextlib.suppress(OSError):
                    content = Path(path).read_text(); skill = Path(path).stem
                    await notify(content, {"type": "skill_load", "skill": skill})
                    log("c3", f"hot-reloaded: {Path(path).name}")
    if transport == "sse":
        import uvicorn
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
        from starlette.responses import JSONResponse
        sse_transport = SseServerTransport("/messages/")
        async def _handle_sse(request):
            async with sse_transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream), anyio.create_task_group() as tg:
                tg.start_soon(server.run, read_stream, write_stream, server.create_initialization_options(experimental_capabilities={"claude/channel": {}}))
                tg.start_soon(_drain_notifications, write_stream); tg.start_soon(_watch_plugins)
        async def _health(request): return JSONResponse({"status": "ok"})
        starlette_app = Starlette(routes=[Route("/health", endpoint=_health), Route("/sse", endpoint=_handle_sse), Mount("/messages/", app=sse_transport.handle_post_message)])
        log("c3", f"MCP SSE server starting on {host}:{port} ...")
        config = uvicorn.Config(starlette_app, host=host, port=port, log_level="warning")
        uv_server = uvicorn.Server(config)
        async with anyio.create_task_group() as tg:
            tg.start_soon(uv_server.serve); tg.start_soon(wa.connect); _boot_proxies(tg)
    else:
        log("c3", "MCP stdio server starting...")
        async with stdio_server() as (read_stream, write_stream), anyio.create_task_group() as tg:
            tg.start_soon(server.run, read_stream, write_stream, server.create_initialization_options(experimental_capabilities={"claude/channel": {}}))
            tg.start_soon(wa.connect); tg.start_soon(_drain_notifications, write_stream)
            tg.start_soon(_watch_plugins); _boot_proxies(tg)

def _find_plugin_dir(name: str) -> str | None:
    import importlib.util
    spec = importlib.util.find_spec(f"c3_{name.replace('-', '_')}")
    if spec and spec.origin: return str(Path(spec.origin).parent)
    bundled = Path(__file__).parent / "plugins" / name
    if bundled.is_dir(): return str(bundled)
    sibling = Path(__file__).parent.parent.parent / f"c3-{name}"
    if (sibling / "config.json").exists() or (sibling / "CLAUDE.md").exists(): return str(sibling)
    return None

def _build_agents_json(plugins_root: Path) -> str:
    """Scan plugins/ dirs and build --agents JSON, inlining claude.md + skills + per-plugin mcpServers."""
    agents: dict = {}
    bundled = Path(__file__).parent / "plugins"
    seen: dict[str, Path] = {}
    # bundled plugins first, user plugins_root overrides by name
    for root in [bundled, plugins_root]:
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if d.is_dir(): seen[d.name] = d
    for name, d in seen.items():
        claude_md = d / "CLAUDE.md"
        try: prompt_text = claude_md.read_text()
        except OSError: continue
        meta: dict = {}
        if (d / "plugin.json").exists():
            with contextlib.suppress(Exception): meta = json.loads((d / "plugin.json").read_text())
        skills = ""
        if (d / "skills").is_dir():
            for sk in sorted((d / "skills").glob("*.md")):
                with contextlib.suppress(OSError): skills += f"\n\n{sk.read_text()}"
        mcp_servers: dict = {}
        if (d / "mcp.json").exists():
            with contextlib.suppress(Exception):
                for sname, scfg in json.loads((d / "mcp.json").read_text()).get("mcpServers", {}).items():
                    if "command" in scfg:
                        cmd = Path(scfg["command"])
                        if not cmd.is_absolute(): scfg["command"] = str((d / cmd).resolve())
                    mcp_servers[sname] = scfg
        entry: dict = {"description": meta.get("description", name), "prompt": prompt_text + skills}
        if mcp_servers: entry["mcpServers"] = mcp_servers
        agents[name] = entry
    return json.dumps(agents)

def _launcher_mode(plugin_dir: str, skip_permissions: bool, sse_url: str | None = None) -> None:
    import shutil
    if not shutil.which("claude"): sys.exit("Error: 'claude' CLI not found.\nInstall: npm install -g @anthropic-ai/claude-code\nAuth:    claude login")
    base = Path(plugin_dir).resolve()
    sd = base / "sessions"; sd.mkdir(exist_ok=True)
    (base / "logs").mkdir(exist_ok=True)
    upstream_file = base / ".upstream.mcp.json"
    if sse_url:
        upstream_cfg = {"mcpServers": {"whatsapp": {"type": "sse", "url": sse_url}}}
    else:
        upstream_cfg = {"mcpServers": {"whatsapp": {"command": "c3-py",
            "args": ["--serve", "--plugin-dir", str(base), "--sessions-dir", str(sd)]}}}
    upstream_file.write_text(json.dumps(upstream_cfg, indent=2))
    # approval-proxy doesn't forward channel notifications (notifications/claude/channel)
    # so we connect c3-py directly — tool approval is handled by the WhatsApp permission relay instead
    mcp_file = base / ".mcp.json"
    mcp_file.write_text(json.dumps(upstream_cfg, indent=2))
    log("c3", "MCP: c3-py direct")
    agents_json = _build_agents_json(base)
    log("c3", f"registered agents: {', '.join(json.loads(agents_json).keys())}")
    claude_args = ["claude", "--mcp-config", str(mcp_file),
                   "--agents", agents_json, "--dangerously-skip-permissions",
                   "--dangerously-load-development-channels", "server:whatsapp"]
    log("c3", "launching Claude Code...")
    os.chdir(base)
    # Auto-accept dialogs using Python pty + delayed Enter keypress
    import subprocess, threading, time, pty, select
    _ansi_re = re.compile(rb'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][0-9A-B]|\x1b\[[\?]?[0-9;]*[hlm]')
    log_file = open(base / "logs" / "claude.log", "ab")
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(claude_args, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
    os.close(slave_fd)
    def _auto_accept():
        for delay in [5, 2, 2]:
            time.sleep(delay)
            try: os.write(master_fd, b"\r")
            except OSError: break
    threading.Thread(target=_auto_accept, daemon=True).start()
    try:
        while True:
            try:
                r, _, _ = select.select([master_fd], [], [], 1.0)
                if r:
                    data = os.read(master_fd, 4096)
                    if not data: break
                    # Raw TUI to stdout (for docker attach)
                    sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
                    # Clean text to log file (strip ANSI)
                    clean = _ansi_re.sub(b'', data).replace(b'\r', b'')
                    if clean.strip(): log_file.write(clean); log_file.flush()
            except OSError: break
            if proc.poll() is not None: break
    finally:
        log_file.close()
        os.close(master_fd)
    sys.exit(proc.returncode or 0)

def _cmd_check(base: Path) -> None:
    w, e = [], []
    for f, msg in [("config.json", "no hosts configured"), ("plugin.json", "using default RBAC"), ("CLAUDE.md", "no custom instructions")]:
        if not (base / f).exists(): w.append(f"{f} missing — {msg}")
        elif f.endswith(".json"):
            with contextlib.suppress(json.JSONDecodeError): json.loads((base / f).read_text())
    g = list((base / "skills").glob("*.md")) if (base / "skills").exists() else []
    if g: print(f"skills/: {len(g)} skill(s): {', '.join(x.stem for x in g)}")
    [print(f"WARN  {x}", file=sys.stderr) for x in w]; [print(f"ERROR {x}", file=sys.stderr) for x in e]
    sys.exit(1) if e else print("OK — plugin dir looks good")

_INIT_CONFIG = {"hosts": [{"jid": "YOURPHONE@s.whatsapp.net", "name": "You"}], "admins": []}

def _cmd_init(base: Path, name: str) -> None:
    base.mkdir(parents=True, exist_ok=True); sessions = base / "sessions"; sessions.mkdir(exist_ok=True)
    def _wif(p, content): (print(f"skip  {p.name} (exists)") if p.exists() else (p.write_text(content), print(f"wrote {p.name}")))
    _wif(base / "config.json", json.dumps(_INIT_CONFIG, indent=2))
    mcp_file = base / ".mcp.json"
    if not mcp_file.exists():
        mcp_file.write_text(json.dumps({"mcpServers": {"whatsapp": {"command": "c3-py",
            "args": ["--serve", "--plugin-dir", str(base.resolve()), "--sessions-dir", str(sessions.resolve())]}}}, indent=2))
        print("wrote .mcp.json")
    else: print("skip  .mcp.json (exists)")
    print(f"\nPlugin '{name}' initialised in {base.resolve()}")
    print("  plugins/games/ + plugins/skills/ → bundled in c3-py package")
    print(f"  Add custom skills to {base}/skills/<name>.md to override bundled ones\n\nNext steps:")
    print(f"  1. Edit config.json — set your WhatsApp JID under 'hosts'\n  2. c3-py auth --sessions-dir {sessions}\n  3. c3-py {base.resolve()}")

def _cmd_auth(sessions_dir: Path) -> None:
    import shutil, subprocess
    if not shutil.which("node"): sys.exit("Error: 'node' not found. Install Node.js 18+ first.")
    sessions_dir.mkdir(parents=True, exist_ok=True); bridge_dir = _BRIDGE.parent
    if not (bridge_dir / "node_modules").exists():
        print("Installing Node.js dependencies (first run)...")
        if subprocess.run(["npm", "install", "--production", "--ignore-scripts", "--silent"], cwd=str(bridge_dir)).returncode != 0: sys.exit("npm install failed.")
    creds = sessions_dir / "creds.json"
    if creds.exists() and creds.stat().st_size > 10:
        print(f"Session found at {sessions_dir}\nAlready authenticated. Delete sessions/ to re-authenticate.\nRun 'c3-py games' to start."); return
    if creds.exists() and creds.stat().st_size <= 10:
        print(f"Stale session found (empty creds). Cleaning up...")
        import shutil as _sh; _sh.rmtree(sessions_dir, ignore_errors=True); sessions_dir.mkdir(parents=True, exist_ok=True)
    print(f"Starting WhatsApp authentication...\nSessions will be saved to: {sessions_dir.resolve()}")
    print("Scan the QR code with your phone → WhatsApp > Linked Devices > Link a Device\n")
    proc = subprocess.Popen(["node", str(_BRIDGE)], env={**os.environ, "SESSIONS_DIR": str(sessions_dir)},
        stderr=sys.stderr, stdout=subprocess.PIPE)
    try:
        for raw in proc.stdout:  # type: ignore[union-attr]
            line = raw.decode().strip()
            if not line: continue
            try:
                obj = json.loads(line)
                if obj.get("event") == "ready":
                    admin = obj.get("adminJid", "")
                    print(f"\n✅ Authenticated as {admin.split(':')[0].split('@')[0]}\nSession saved. Run 'c3-py games' to start.")
                    proc.terminate(); return
            except Exception: pass
    except KeyboardInterrupt: print("\nCancelled.")
    finally: proc.terminate(); proc.wait()

def _send_test_message(sessions_dir: Path, jid: str, text: str = "✅ c3-py setup complete — bot is online!") -> bool:
    """Briefly start the bridge and send one message. Returns True on success."""
    import subprocess, threading
    proc = subprocess.Popen(["node", str(_BRIDGE)], env={**os.environ, "SESSIONS_DIR": str(sessions_dir)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    sent = threading.Event(); ok = threading.Event()
    def _writer():
        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                line = raw.decode().strip()
                if not line: continue
                try:
                    obj = json.loads(line)
                    if obj.get("event") == "ready" and not sent.is_set():
                        sent.set(); cmd = json.dumps({"cmd": "send", "jid": jid, "text": text}) + "\n"
                        proc.stdin.write(cmd.encode()); proc.stdin.flush()  # type: ignore[union-attr]
                    elif obj.get("event") == "sent": ok.set(); return
                except Exception: pass
        except Exception: pass
    t = threading.Thread(target=_writer, daemon=True); t.start(); t.join(timeout=_cfg.test_message_timeout)
    proc.terminate(); proc.wait(); return ok.is_set()

def _check_prereqs() -> list[str]:
    """Check prerequisites and return list of issues."""
    import shutil, subprocess
    issues = []
    if not shutil.which("node"):
        issues.append("Node.js not found — install Node.js 18+ (https://nodejs.org)")
    else:
        try:
            v = subprocess.check_output(["node", "--version"], text=True).strip()
            major = int(v.lstrip("v").split(".")[0])
            if major < 18: issues.append(f"Node.js {v} is too old — need 18+")
        except Exception: pass
    if not shutil.which("claude"):
        issues.append("Claude Code CLI not found — install: curl -fsSL https://claude.ai/install.sh | bash")
    if not shutil.which("npm"):
        issues.append("npm not found — comes with Node.js")
    return issues

def _cmd_setup(base: Path) -> None:
    import shutil
    print("\n🚀  c3-py setup wizard\n" + "─" * 40)
    issues = _check_prereqs()
    if issues:
        print("\n⚠️  Prerequisites check:")
        for i in issues: print(f"   ❌ {i}")
        if input("\n   Continue anyway? [y/N] ").strip().lower() != "y": sys.exit(1)
    else:
        print("   ✅ All prerequisites found")
    print(f"\n📁  Plugin directory: {base.resolve()}")
    answer = input("   Use this directory? [Y/n] ").strip().lower()
    if answer in ("n", "no"): base = Path(input("   Enter path: ").strip()).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True); sessions = base / "sessions"; sessions.mkdir(exist_ok=True)
    print("\n👤  Your WhatsApp account details:")
    host_jid = input("   Phone number JID (e.g. 911234567890@s.whatsapp.net): ").strip()
    host_name = input("   Display name [Host]: ").strip() or "Host"
    print("\n🤖  Claude Code CLI:"); claude_path = shutil.which("claude")
    if claude_path: print(f"   Found at {claude_path}")
    else:
        print("   'claude' not found in PATH.")
        custom_claude = input("   Full path to claude binary (leave blank to skip): ").strip()
        if custom_claude:
            os.environ["PATH"] = str(Path(custom_claude).parent) + os.pathsep + os.environ["PATH"]
            claude_path = custom_claude
    print("\n📱  WhatsApp authentication:"); creds = sessions / "creds.json"
    if creds.exists(): print("   Already authenticated ✅")
    else:
        input("   Press Enter to start QR scan..."); _cmd_auth(sessions)
        if not creds.exists(): print("\n⚠️  Authentication did not complete. Run 'c3-py auth' later.")
    bundled_plugins = sorted((Path(__file__).parent / "plugins" / "games" / "skills").glob("*.md"))
    startup_plugins: list[str] = []
    if bundled_plugins:
        print("\n🧩  Available plugins:")
        for i, p in enumerate(bundled_plugins, 1): print(f"   {i}. {p.stem}")
        for token in re.split(r"[,\s]+", input("   Auto-load at startup (comma-separated numbers or names, blank=none): ").strip()):
            if not token.strip(): continue
            idx = int(token) - 1 if token.strip().isdigit() else -1
            if 0 <= idx < len(bundled_plugins): startup_plugins.append(bundled_plugins[idx].stem)
            elif not token.strip().isdigit(): startup_plugins.append(token.strip().removesuffix(".md"))
    config_path = base / "config.json"; existing: dict = {}
    if config_path.exists():
        with contextlib.suppress(Exception): existing = json.loads(config_path.read_text())
    existing["hosts"] = [{"jid": host_jid, "name": host_name}]
    if startup_plugins: existing["startup_plugins"] = startup_plugins
    config_path.write_text(json.dumps(existing, indent=2)); print(f"\n✅  config.json saved → {config_path}")
    mcp_file = base / ".mcp.json"
    if not mcp_file.exists():
        mcp_cfg = {"mcpServers": {"whatsapp": {"command": "c3-py",
            "args": ["--serve", "--plugin-dir", str(base), "--sessions-dir", str(sessions)]}}}
        mcp_file.write_text(json.dumps(mcp_cfg, indent=2)); print(f"✅  .mcp.json saved → {mcp_file}")
    else: print("   .mcp.json already exists — skipped")
    print("\n📨  Send a test WhatsApp message?")
    if creds.exists() and host_jid:
        answer = input(f"   Send to {host_jid}? [Y/n] ").strip().lower()
        if answer not in ("n", "no"):
            print("   Sending...", end=" ", flush=True)
            ok = _send_test_message(sessions, host_jid)
            print("✅ delivered!" if ok else "⚠️  timed out (bridge may need a moment — try again later)")
    else: print("   Skipped — authenticate first.")
    print("\n" + "─" * 40 + "\n🎉  Setup complete!\n")
    print(f"   Plugin dir : {base}\n   Sessions   : {sessions}")
    if startup_plugins: print(f"   Plugins    : {', '.join(startup_plugins)}")
    print()
    if claude_path and input("🚀  Launch Claude now? [Y/n] ").strip().lower() not in ("n", "no"): _launcher_mode(str(base), False)
    elif not claude_path: print("   Run 'c3-py games' (or your plugin name) to start.")

_PLUGIN_TEMPLATES = {
    "CLAUDE.md": "## {name}\n\nDescribe what this plugin does and what capabilities it adds.\n",
    "plugin.json": '{{\n  "access": {{\n    "commands": {{}},\n    "dm":    ["hosts", "admins"],\n    "group": []\n  }}\n}}\n',
    ".memory_schema": '{{\n  "example_entity": {{\n    "fields": ["field1", "field2"],\n    "example": {{"plugin": "{name}", "entity": "example_entity", "field1": "value1"}}\n  }}\n}}\n',
    "skills/{name}.md": "## {name} skill\n\nInstructions for Claude when this skill is active.\n",
}

def _cmd_plugin_new(dest: Path) -> None:
    name = dest.name; dest.mkdir(parents=True, exist_ok=True)
    (dest / "skills").mkdir(exist_ok=True)
    for fname_tpl, content_tpl in _PLUGIN_TEMPLATES.items():
        fname = fname_tpl.replace("{name}", name); p = dest / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists(): print(f"skip  {fname} (exists)")
        else: p.write_text(content_tpl.format(name=name)); print(f"wrote {fname}")
    print(f"\nPlugin '{name}' created at {dest.resolve()}")
    for line in [f"  Edit {name}/CLAUDE.md          — boot-time capability description",
                 f"  Edit {name}/skills/{name}.md   — skill rules loaded on demand",
                 f"  Edit {name}/.memory_schema      — entity schemas",
                 f"  Add  {name}/.mcp               — declare extra MCP servers (optional)",
                 f"  Add  {name}/.crons             — scheduled jobs (optional)"]: print(line)

_app = typer.Typer(name="c3-py", help="c3 WhatsApp agent — launcher and MCP server", add_completion=False)

@_app.command("setup")
def _cli_setup(plugin_dir: str = typer.Option(".", "--plugin-dir", "-d", help="Plugin directory")):
    """Interactive e2e setup wizard."""; _cmd_setup(Path(plugin_dir))

@_app.command("auth")
def _cli_auth(sessions_dir: str = typer.Option("sessions", "--sessions-dir", "-s")):
    """Authenticate WhatsApp (scan QR code)."""; _cmd_auth(Path(sessions_dir))

@_app.command("wa-login")
def _cli_wa_login():
    """WhatsApp login — scan QR code (Docker shortcut)."""; _cmd_auth(Path("/plugin/sessions"))

@_app.command("check")
def _cli_check(plugin_dir: str = typer.Option(".", "--plugin-dir", "-d")):
    """Validate plugin directory."""; _cmd_check(Path(plugin_dir))

@_app.command("init")
def _cli_init(plugin_dir: str = typer.Option(".", "--plugin-dir", "-d"), name: str = typer.Option("my-plugin", "--name", "-n")):
    """Scaffold a new plugin directory."""; _cmd_init(Path(plugin_dir), name)

_plugin_app = typer.Typer(help="Plugin management commands.")
_app.add_typer(_plugin_app, name="plugin")

@_plugin_app.command("install")
def _cli_plugin_install(
    url: str = typer.Argument(..., help="GitHub URL or shorthand (user/repo)"),
    plugin_dir: str = typer.Option(".", "--plugin-dir", "-d"),
):
    """Install a plugin from a GitHub URL."""
    import subprocess, tempfile
    if "/" in url and not url.startswith("http"):
        url = f"https://github.com/{url}"
    name = url.rstrip("/").split("/")[-1].removeprefix("c3-").removeprefix("c3py-")
    dest = Path(plugin_dir) / name
    if dest.exists(): sys.exit(f"Error: plugin '{name}' already exists at {dest}")
    print(f"Installing {name} from {url}...")
    with tempfile.TemporaryDirectory() as tmp:
        if subprocess.run(["git", "clone", "--depth", "1", url, tmp], capture_output=True).returncode != 0:
            sys.exit(f"Error: could not clone {url}")
        # Look for plugin structure: CLAUDE.md + skills/ or plugin.json
        src = Path(tmp)
        if (src / "CLAUDE.md").exists(): src_dir = src
        elif (src / name).is_dir() and (src / name / "CLAUDE.md").exists(): src_dir = src / name
        else:
            candidates = [d for d in src.iterdir() if d.is_dir() and (d / "CLAUDE.md").exists()]
            src_dir = candidates[0] if candidates else src
        import shutil; shutil.copytree(str(src_dir), str(dest), dirs_exist_ok=True)
    # Clean up git artifacts
    for g in [dest / ".git", dest / ".github"]:
        if g.exists(): import shutil; shutil.rmtree(g)
    print(f"✅ Plugin '{name}' installed at {dest}")
    if (dest / "CLAUDE.md").exists(): print(f"   CLAUDE.md found ✓")
    if (dest / "skills").is_dir(): print(f"   skills/: {len(list((dest / 'skills').glob('*.md')))} skill(s)")
    if (dest / "plugin.json").exists(): print(f"   plugin.json found ✓")

@_plugin_app.command("new")
def _cli_plugin_new(
    name: str = typer.Argument(..., help="Plugin name"),
    plugin_dir: str = typer.Option(".", "--plugin-dir", "-d"),
):
    """Create a new plugin scaffold."""
    _cmd_plugin_new(Path(plugin_dir) / name)

@_app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    plugin: Optional[str] = typer.Argument(None, help="Plugin name or path (e.g. 'games')"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip Claude permission prompts"),
    serve: bool = typer.Option(False, "--serve", help="Run as MCP server (called by Claude Code)"),
    plugin_dir: Optional[str] = typer.Option(None, "--plugin-dir", "-d"),
    sessions_dir: Optional[str] = typer.Option(None, "--sessions-dir", "-s"),
    sse: bool = typer.Option(False, "--sse", help="SSE transport (Docker)"),
    sse_url: Optional[str] = typer.Option(None, "--sse-url", help="Connect to remote MCP server (e.g. http://localhost:3000/sse)"),
    host: str = typer.Option(_cfg.host, "--host"),
    port: int = typer.Option(_cfg.port, "--port"),
) -> None:
    if ctx.invoked_subcommand: return
    if plugin == "wa-login": _cmd_auth(Path(sessions_dir or "/plugin/sessions")); return
    pdir: str | None = plugin_dir
    if plugin and not serve:
        p = Path(plugin)
        if p.exists(): pdir = str(p.resolve())
        else:
            pdir = _find_plugin_dir(plugin)
            if not pdir:
                typer.echo(f"Error: plugin '{plugin}' not found.\nTry: pip install c3-{plugin}", err=True)
                raise typer.Exit(1)
        _launcher_mode(pdir, yes, sse_url=sse_url); return
    if pdir is None: pdir = "."
    sdir = sessions_dir
    if sdir is None:
        candidate = Path(pdir) / "sessions"
        if candidate.exists(): sdir = str(candidate)
    asyncio.run(create_channel(BaileysAdapter(sessions_dir=sdir), plugin_dir=pdir,
        transport="sse" if sse else "stdio", host=host, port=port))

def cli() -> None: _app()

if __name__ == "__main__":
    cli()
