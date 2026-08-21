from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import discord

from .database import Database


def split_discord_message(content: str, limit: int = 1850) -> list[str]:
    if len(content) <= limit:
        return [content]
    chunks: list[str] = []
    remaining = content
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind("。", 0, limit)
            if cut >= limit // 2:
                cut += 1
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


class AnkoDiscordClient(discord.Client):
    def __init__(self, database: Database) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.database = database

    async def on_message(self, message: discord.Message) -> None:
        if not self.user or message.author.id == self.user.id or message.author.bot:
            return
        campaign_id = self.database.campaign_for_thread(message.channel.id)
        if not campaign_id:
            return
        self.database.record_discord_message(
            message.id,
            campaign_id,
            message.author.id,
            message.author.display_name,
            message.content,
            "inbound",
        )
        self.database.create_chat_message(
            campaign_id,
            "discord",
            str(message.author.id),
            message.author.display_name,
            message.content,
            "inbound",
            "received",
            str(message.id),
        )


class DiscordService:
    def __init__(self, database: Database, token: str | None) -> None:
        self.database = database
        self.token = token
        self.client = AnkoDiscordClient(database)
        self._client_task: asyncio.Task[Any] | None = None
        self._outbox_task: asyncio.Task[Any] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.token)

    @property
    def ready(self) -> bool:
        return self.client.is_ready()

    async def start(self) -> None:
        if not self.token or self._client_task:
            return
        self._client_task = asyncio.create_task(
            self.client.start(self.token), name="discord-client"
        )
        self._outbox_task = asyncio.create_task(self._outbox_loop(), name="discord-outbox")

    async def stop(self) -> None:
        if self._outbox_task:
            self._outbox_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._outbox_task
        if not self.client.is_closed():
            await self.client.close()
        if self._client_task:
            with suppress(asyncio.CancelledError, discord.DiscordException):
                await self._client_task

    async def create_forum_post(
        self, campaign_id: str, forum_channel_id: int, post_name: str
    ) -> int:
        if not self.ready:
            raise RuntimeError("Discord Bot 尚未连接")
        channel = self.client.get_channel(forum_channel_id)
        if channel is None:
            channel = await self.client.fetch_channel(forum_channel_id)
        if not isinstance(channel, discord.ForumChannel):
            raise RuntimeError("选择的频道不是 Discord 论坛频道")
        created = await channel.create_thread(
            name=post_name,
            content=f"🎲 **{post_name}**\n\n本帖已与 Anko Director 本地存档绑定。",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        thread = created.thread if hasattr(created, "thread") else created
        self.database.bind_discord(campaign_id, forum_channel_id, thread.id)
        return thread.id

    async def publish_item(self, item: dict[str, Any]) -> list[int]:
        thread_id = item.get("discord_thread_id")
        if not thread_id:
            raise RuntimeError("本局尚未绑定 Discord 帖子")
        if not self.ready:
            raise RuntimeError("Discord Bot 当前未连接")
        channel = self.client.get_channel(int(thread_id))
        if channel is None:
            channel = await self.client.fetch_channel(int(thread_id))
        marker = f"`Anko 事件 {item['event_id'][:8]}`"
        chunks = split_discord_message(item["content"])
        message_ids: list[int] = []
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            prefix = f"**剧情发布 {index}/{total}**\n" if total > 1 else "**剧情发布**\n"
            suffix = f"\n\n{marker}" if index == total else ""
            message = await channel.send(
                prefix + chunk + suffix,
                allowed_mentions=discord.AllowedMentions.none(),
                silent=True,
            )
            message_ids.append(message.id)
            self.database.record_discord_message(
                message.id,
                item["campaign_id"],
                self.client.user.id if self.client.user else 0,
                self.client.user.display_name if self.client.user else "Anko Director",
                chunk,
                "outbound",
            )
            self.database.create_chat_message(
                item["campaign_id"],
                "discord",
                str(self.client.user.id if self.client.user else 0),
                self.client.user.display_name if self.client.user else "Anko Director",
                chunk,
                "outbound",
                "sent",
                str(message.id),
                {"event_id": item["event_id"], "part": index, "total": total},
            )
        return message_ids

    async def flush_outbox_once(self) -> None:
        if not self.ready:
            return
        for item in self.database.pending_outbox():
            try:
                message_ids = await self.publish_item(item)
            except Exception as exc:  # Discord errors are persisted for later retry
                self.database.mark_outbox_failed(item["id"], str(exc))
            else:
                self.database.mark_outbox_sent(item["id"], message_ids)
        for item in self.database.pending_chat_messages():
            try:
                message_id = await self.publish_chat_item(item)
            except Exception as exc:
                self.database.mark_chat_failed(item["id"], str(exc))
            else:
                self.database.mark_chat_sent(item["id"], message_id)

    async def publish_chat_item(self, item: dict[str, Any]) -> int:
        thread_id = item.get("discord_thread_id")
        if not thread_id:
            raise RuntimeError("本局尚未绑定 Discord 帖子")
        if not self.ready:
            raise RuntimeError("Discord Bot 当前未连接")
        channel = self.client.get_channel(int(thread_id))
        if channel is None:
            channel = await self.client.fetch_channel(int(thread_id))
        chunks = split_discord_message(item["content"])
        last_message_id = 0
        for index, chunk in enumerate(chunks, start=1):
            prefix = "**导演（本地）**\n"
            if len(chunks) > 1:
                prefix += f"*{index}/{len(chunks)}*\n"
            message = await channel.send(
                prefix + chunk,
                allowed_mentions=discord.AllowedMentions.none(),
                silent=True,
            )
            last_message_id = message.id
            self.database.record_discord_message(
                message.id,
                item["campaign_id"],
                self.client.user.id if self.client.user else 0,
                "导演（本地）",
                chunk,
                "outbound",
            )
        return last_message_id

    async def _outbox_loop(self) -> None:
        while True:
            await asyncio.sleep(2)
            await self.flush_outbox_once()
