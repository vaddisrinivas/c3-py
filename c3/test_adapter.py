"""TestAdapter — ChatAdapter for integration testing. Replaces BaileysAdapter."""
from __future__ import annotations
import asyncio, json, uuid
from c3.agent import ChatAdapter, GroupMember, Message, log


class TestAdapter(ChatAdapter):
    """ChatAdapter that captures outbound messages and accepts injected inbound messages."""

    def __init__(self, host_jid: str = "test-host@test"):
        self.admin_jid = host_jid
        self.inbox: asyncio.Queue[Message] = asyncio.Queue()
        self.outbox: asyncio.Queue[dict] = asyncio.Queue()
        self._members = [
            GroupMember(jid="alice@test", name="Alice", is_admin=False),
            GroupMember(jid="bob@test", name="Bob", is_admin=False),
        ]
        self._group_jid = "group-test"
        self._poll_counter = 0

    async def connect(self) -> None:
        if self.on_ready:
            await self.on_ready()
        while True:
            msg = await self.inbox.get()
            if self.on_message:
                await self.on_message(msg)

    async def send(self, jid: str, text: str) -> None:
        await self.outbox.put({"type": "text", "jid": jid, "text": text})
        log("test", f"send → {jid}: {text[:80]}")

    async def send_poll(self, jid: str, question: str, options: list[str]) -> str:
        self._poll_counter += 1
        poll_id = f"test-poll-{self._poll_counter}"
        await self.outbox.put({"type": "poll", "jid": jid, "question": question, "options": options, "poll_id": poll_id})
        return poll_id

    async def resolve_group(self, invite_link: str) -> str:
        return self._group_jid

    async def get_group_members(self, group_jid: str) -> list[GroupMember]:
        return self._members

    def get_name(self, jid: str) -> str:
        return jid.split("@")[0] if "@" in jid else jid

    def is_group_id(self, id: str) -> bool:
        return id.startswith("group-")

    def is_valid_invite(self, link: str) -> bool:
        return link.startswith("test://") or "chat.whatsapp.com" in link

    def extract_name(self, id: str) -> str:
        return id.split("@")[0] if "@" in id else id

    # -- Helpers for test injection --

    async def inject(self, text: str, sender: str | None = None, is_group: bool = False, **kw) -> None:
        """Inject a message as if it came from the chat platform."""
        sender = sender or self.admin_jid
        msg = Message(
            jid=self._group_jid if is_group else sender,
            sender=sender, push_name=self.extract_name(sender),
            text=text, timestamp=0, is_group=is_group, **kw)
        await self.inbox.put(msg)

    async def drain_outbox(self, timeout: float = 0.5) -> list[dict]:
        """Drain all messages from the outbox."""
        items = []
        try:
            while True:
                items.append(await asyncio.wait_for(self.outbox.get(), timeout=timeout))
        except (asyncio.TimeoutError, TimeoutError):
            pass
        return items

    def starlette_routes(self):
        """Return Starlette routes for HTTP test control (used with --test --sse)."""
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def _inject(request: Request) -> JSONResponse:
            body = await request.json()
            await self.inject(body.get("text", ""), sender=body.get("sender"), is_group=body.get("is_group", False))
            return JSONResponse({"status": "injected"})

        async def _outbox(request: Request) -> JSONResponse:
            items = await self.drain_outbox(timeout=0.1)
            return JSONResponse(items)

        async def _clear(request: Request) -> JSONResponse:
            while not self.outbox.empty():
                self.outbox.get_nowait()
            return JSONResponse({"status": "cleared"})

        async def _poll_vote(request: Request) -> JSONResponse:
            poll_id = request.path_params["poll_id"]
            body = await request.json()
            if self.on_poll_update:
                await self.on_poll_update(poll_id, body.get("tally", {}))
            return JSONResponse({"status": "voted"})

        return [
            Route("/test/inject", endpoint=_inject, methods=["POST"]),
            Route("/test/outbox", endpoint=_outbox, methods=["GET"]),
            Route("/test/outbox", endpoint=_clear, methods=["DELETE"]),
            Route("/test/poll/{poll_id}/vote", endpoint=_poll_vote, methods=["POST"]),
        ]
