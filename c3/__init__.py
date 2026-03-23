"""c3-py — WhatsApp × Claude Code MCP server."""
from .agent import (
    AccessPolicy, AppConfig, BaileysAdapter, ChannelCore, GroupMember,
    HostConfig, JidMask, PluginController, PluginManifest, PluginSession,
    SessionEngine, WAAdapter, WAMessage, create_channel, log, parse_duration, pick,
)

__version__ = "0.1.0"
__all__ = [
    "__version__", "AccessPolicy", "AppConfig", "BaileysAdapter", "ChannelCore",
    "GroupMember", "HostConfig", "JidMask", "PluginController", "PluginManifest",
    "PluginSession", "SessionEngine", "WAAdapter", "WAMessage",
    "create_channel", "log", "parse_duration", "pick",
]
