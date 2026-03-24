"""c3-py — Chat AI apps framework via MCP."""
from .agent import (
    AccessControl, AccessPolicy, AppManifest, AppMCPProxy,
    AppConfig, BaileysAdapter, BaileysError, BaileysDisconnectedError, BaileysTimeoutError,
    ChatAdapter, ChannelCore, GroupMember,
    HostConfig, Message, SessionEngine,
    WAAdapter, WAMessage,  # compat aliases
    create_channel, log, parse_duration, pick,
)

__version__ = "0.2.0"
__all__ = [
    "__version__", "AccessControl", "AccessPolicy",
    "AppManifest", "AppMCPProxy",
    "AppConfig", "BaileysAdapter", "BaileysError", "BaileysDisconnectedError", "BaileysTimeoutError",
    "ChatAdapter", "ChannelCore", "GroupMember", "HostConfig", "Message",
    "SessionEngine", "WAAdapter", "WAMessage",
    "create_channel", "log", "parse_duration", "pick",
]
