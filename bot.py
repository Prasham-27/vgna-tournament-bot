"""Standalone VGNA tournament registration Discord bot."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from tournament.config import Settings
from tournament.db import TournamentDatabase
from tournament.domain import normalize_team_name, validate_team_players
from tournament.panel import registration_embed


log = logging.getLogger("vgna_tournament")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("event", "team_id", "guild_id", "user_id", "error_class", "channel_id", "category_id", "category_name", "message_id", "panel_action"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


class TeamNameModal(discord.ui.Modal, title="Register a team"):
    team_name = discord.ui.TextInput(
        label="Team name",
        placeholder="Enter a team name",
        min_length=2,
        max_length=80,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        if not isinstance(bot, TournamentBot) or interaction.guild_id != bot.settings.guild_id:
            await interaction.response.send_message("This registration panel is not active in this server.", ephemeral=True)
            return
        name = normalize_team_name(str(self.team_name.value))
        if not name:
            await interaction.response.send_message("Please enter a team name.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Select exactly five current server members, then submit.",
            view=TeamRegistrationView(bot, interaction.user.id, interaction.guild_id, name),
            ephemeral=True,
        )


class TeamRegistrationView(discord.ui.View):
    def __init__(self, bot: "TournamentBot", owner_id: int, guild_id: int, team_name: str):
        super().__init__(timeout=600)
        self.bot = bot
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.team_name = team_name
        self.request_id = f"{owner_id}-{id(self)}"
        self.member_select = discord.ui.UserSelect(
            placeholder="Select exactly five server members",
            min_values=5,
            max_values=5,
            row=0,
        )
        self.add_item(self.member_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the person who started this form can use it.", ephemeral=True)
            return False
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("This form belongs to another server.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Submit team", style=discord.ButtonStyle.success, row=1)
    async def submit(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        selected = [str(member.id) for member in self.member_select.values]
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            await interaction.followup.send("I cannot access the configured tournament server right now.", ephemeral=True)
            return
        members = {}
        for user_id in selected:
            member = guild.get_member(int(user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(user_id))
                except (discord.NotFound, discord.HTTPException):
                    member = None
            if member is not None:
                members[user_id] = member
        errors = validate_team_players(selected, members)
        if errors:
            await interaction.followup.send("\n".join(f"• {error}" for error in errors), ephemeral=True)
            return
        now = self.bot.now()
        try:
            result = await self.bot.db.create_team(
                guild_id=self.guild_id,
                team_name=self.team_name,
                registrar_user_id=interaction.user.id,
                registrar_display_name=interaction.user.display_name,
                members=[(member.id, member.display_name) for member in members.values()],
                now=now,
                team_deadline=self.bot.settings.team_deadline,
                max_teams=self.bot.settings.max_teams,
                confirmation_ttl_hours=self.bot.settings.confirmation_ttl_hours,
                idempotency_key=self.request_id,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if result["idempotent"]:
            await interaction.followup.send("This form was already submitted; I kept the original registration.", ephemeral=True)
            return
        team = await self.bot.db.get_team(result["id"])
        await interaction.followup.send(
            f"Team **{self.team_name}** is pending confirmation. I’m sending each member a private approval request.",
            ephemeral=True,
        )
        if team:
            await self.bot.send_confirmation_dms(team)
        await self.bot.refresh_panel()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Team registration cancelled.", view=None)


class DeclineModal(discord.ui.Modal, title="Decline team confirmation"):
    reason = discord.ui.TextInput(
        label="Optional private reason",
        placeholder="Optional context shared privately with the team registrar.",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, team_id: int, bot: "TournamentBot"):
        super().__init__()
        self.team_id = team_id
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.bot.process_confirmation(
            self.team_id, interaction.user.id, "decline", str(self.reason.value or "").strip() or None
        )
        await interaction.followup.send(self.bot.confirmation_response_text(result), ephemeral=True)


class ConfirmationView(discord.ui.View):
    def __init__(self, bot: "TournamentBot", team_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.team_id = team_id
        approve = discord.ui.Button(
            label="Approve", style=discord.ButtonStyle.success,
            custom_id=f"tourney:confirm:{team_id}:approve"
        )
        decline = discord.ui.Button(
            label="Decline", style=discord.ButtonStyle.danger,
            custom_id=f"tourney:confirm:{team_id}:decline"
        )
        approve.callback = self.approve
        decline.callback = self.decline
        self.add_item(approve)
        self.add_item(decline)

    async def approve(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.bot.process_confirmation(self.team_id, interaction.user.id, "approve", None)
        await interaction.followup.send(self.bot.confirmation_response_text(result), ephemeral=True)

    async def decline(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(DeclineModal(self.team_id, self.bot))


class RegistrationPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Register a Team", style=discord.ButtonStyle.primary, custom_id="tourney:register_team", row=0
    )
    async def register_team(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        bot = interaction.client
        if not isinstance(bot, TournamentBot) or interaction.guild_id != bot.settings.guild_id:
            await interaction.response.send_message("This panel is not active in the configured server.", ephemeral=True)
            return
        await interaction.response.send_modal(TeamNameModal())

    @discord.ui.button(
        label="Join Substitute Pool", style=discord.ButtonStyle.success, custom_id="tourney:join_substitute", row=0
    )
    async def join_substitute(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        bot = interaction.client
        if not isinstance(bot, TournamentBot) or interaction.guild_id != bot.settings.guild_id:
            await interaction.response.send_message("This panel is not active in the configured server.", ephemeral=True)
            return
        await bot.join_substitute_interaction(interaction)

    @discord.ui.button(
        label="Leave Substitute Pool", style=discord.ButtonStyle.secondary, custom_id="tourney:leave_substitute", row=0
    )
    async def leave_substitute(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        bot = interaction.client
        if not isinstance(bot, TournamentBot) or interaction.guild_id != bot.settings.guild_id:
            await interaction.response.send_message("This panel is not active in the configured server.", ephemeral=True)
            return
        await bot.leave_substitute_interaction(interaction)


class TournamentBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.db = TournamentDatabase(settings.database_url)
        self.registration_channel: discord.TextChannel | None = None
        self._ready_once = False
        self._expiry_task: asyncio.Task | None = None
        self._confirmation_views: set[int] = set()

    def now(self) -> datetime:
        return datetime.now(timezone.utc).astimezone(self.settings.timezone)

    async def setup_hook(self) -> None:
        await self.db.connect()
        self.add_view(RegistrationPanelView())

    def configured_guild(self) -> discord.Guild:
        guild = self.get_guild(self.settings.guild_id)
        if guild is None:
            raise RuntimeError(f"Configured guild {self.settings.guild_id} is not available to this bot")
        return guild

    def validate_permissions(self, guild: discord.Guild) -> None:
        me = guild.me
        if me is None:
            raise RuntimeError("Discord has not provided the bot member for the configured guild")
        permissions = me.guild_permissions
        required = {
            "manage_channels": permissions.manage_channels,
            "view_channel": permissions.view_channel,
            "send_messages": permissions.send_messages,
            "embed_links": permissions.embed_links,
            "read_message_history": permissions.read_message_history,
        }
        missing = [name for name, present in required.items() if not present]
        if missing:
            raise RuntimeError("Bot is missing required tournament channel permissions: " + ", ".join(missing))

    async def ensure_registration_channel(self, guild: discord.Guild) -> discord.TextChannel:
        # The registrations channel is the durable anchor. If an organizer has
        # renamed or repositioned its category, adopt that category instead of
        # recreating/moving anything on every restart.
        channel = discord.utils.find(lambda item: item.name == "registrations", guild.text_channels)
        category = channel.category if channel is not None else None
        if category is None:
            category_names = {
                self.settings.category_name,
                "Tourney Controls",  # original default
                "Toruney Info",      # organizer-customized name used by this server
            }
            category = discord.utils.find(lambda item: item.name in category_names, guild.categories)
        if category is None:
            category = await guild.create_category(
                self.settings.category_name, reason="VGNA tournament registration setup"
            )
        if channel is None:
            channel = await guild.create_text_channel(
                "registrations", category=category, reason="VGNA tournament registration setup"
            )
        log.info(
            "registration_surface_ready",
            extra={
                "event": "registration_surface_ready",
                "guild_id": guild.id,
                "channel_id": channel.id,
                "category_id": category.id if category else None,
                "category_name": category.name if category else None,
            },
        )
        return channel

    async def setup_registration_surface(self, guild: discord.Guild) -> None:
        self.validate_permissions(guild)
        self.registration_channel = await self.ensure_registration_channel(guild)
        await self.refresh_panel()

    async def refresh_panel(self) -> None:
        if self.registration_channel is None:
            return
        snapshot = await self.db.panel_snapshot(self.settings.guild_id)
        embed = registration_embed(snapshot, self.settings)
        view = RegistrationPanelView()
        message_id = await self.db.get_setting(self.settings.guild_id, "registration_panel_message_id")
        message = None
        if message_id:
            try:
                message = await self.registration_channel.fetch_message(int(message_id))
            except (discord.NotFound, discord.HTTPException, ValueError):
                message = None
        if message is None:
            async for candidate in self.registration_channel.history(limit=100):
                if candidate.author.id == self.user.id and candidate.embeds and candidate.embeds[0].title == embed.title:
                    message = candidate
                    break
        if message is None:
            message = await self.registration_channel.send(embed=embed, view=view)
            panel_action = "created"
        else:
            await message.edit(embed=embed, view=view)
            panel_action = "updated"
        await self.db.set_setting(self.settings.guild_id, "registration_panel_message_id", str(message.id), self.now())
        log.info(
            "registration_panel_ready",
            extra={
                "event": "registration_panel_ready",
                "guild_id": self.settings.guild_id,
                "channel_id": self.registration_channel.id,
                "message_id": message.id,
                "panel_action": panel_action,
            },
        )

    async def send_confirmation_dms(
        self, team: dict[str, Any], *, retry_unsent: bool = True, notify_on_failure: bool = True
    ) -> None:
        guild = self.configured_guild()
        names = {str(member["user_id"]): member["display_name"] for member in team["members"]}
        roster = ", ".join(names.values())
        confirmation_by_user = {str(item["user_id"]): item for item in team["confirmations"]}
        team_id = int(team["id"])
        if team_id not in self._confirmation_views:
            self.add_view(ConfirmationView(self, team_id))
            self._confirmation_views.add(team_id)
        for member in team["members"]:
            user_id = int(member["user_id"])
            confirmation = confirmation_by_user.get(str(user_id), {})
            if confirmation.get("status") != "pending":
                continue
            if retry_unsent and confirmation.get("dm_status") == "sent":
                continue
            live_member = guild.get_member(user_id)
            ok = False
            failure = None
            try:
                if live_member is None or live_member.bot:
                    raise RuntimeError("member_unavailable")
                await live_member.send(
                    f"You were selected for **{team['team_name']}**.\n\n"
                    f"Other members: {roster}\n"
                    f"Submitted by: {team['registrar_display_name']}\n"
                    f"Please respond by **{team['confirmation_deadline'].astimezone(self.settings.timezone):%B %-d, %Y at %-I:%M:%S %p %Z}**. "
                    "If the window expires or any member declines, the team remains unconfirmed.",
                    view=ConfirmationView(self, team_id),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                ok = True
            except discord.Forbidden:
                failure = "forbidden"
            except discord.HTTPException:
                failure = "http_error"
            except RuntimeError as exc:
                failure = str(exc)
            await self.db.record_dm_delivery(team_id, user_id, ok=ok, failure_code=failure, now=self.now())
        failures = await self.db.get_team(team_id)
        if notify_on_failure and failures and any(item["dm_status"] == "failed" for item in failures["confirmations"]):
            await self.notify_registrar(
                failures,
                "One or more private approval messages could not be delivered. The team is still unconfirmed; "
                "please check that every selected member can receive DMs.",
            )

    async def notify_registrar(self, team: dict[str, Any], content: str) -> None:
        try:
            user = await self.fetch_user(int(team["registrar_user_id"]))
            await user.send(content, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            log.warning("registrar_notification_failed", extra={"event": "registrar_notification_failed", "team_id": team["id"]})

    async def process_confirmation(self, team_id: int, user_id: int, decision: str, reason: str | None) -> dict[str, Any]:
        result = await self.db.respond_confirmation(
            team_id=team_id, user_id=user_id, decision=decision, private_reason=reason, now=self.now()
        )
        team = result.get("team")
        if team and result["outcome"] == "confirmed":
            await self.notify_registrar(team, f"Team **{team['team_name']}** is fully confirmed.")
            await self.refresh_panel()
        elif team and result["outcome"] == "declined":
            private_note = f"\nPrivate reason from the member: {reason}" if reason else ""
            await self.notify_registrar(
                team,
                "A selected member declined the team confirmation. The team remains unconfirmed and its slot is free."
                + private_note,
            )
            await self.refresh_panel()
        return result

    @staticmethod
    def confirmation_response_text(result: dict[str, Any]) -> str:
        outcome = result.get("outcome")
        return {
            "confirmed": "Your approval was recorded. All five members have approved; the team is confirmed.",
            "approved": "Your approval was recorded. The team is waiting for the remaining members.",
            "declined": "Your decline was recorded privately. The team remains unconfirmed.",
            "idempotent": "This confirmation action was already recorded.",
            "not_found": "This confirmation request is no longer available.",
        }.get(outcome, "Your confirmation response was recorded.")

    async def join_substitute_interaction(self, interaction: discord.Interaction) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or member.guild.id != self.settings.guild_id:
            await interaction.response.send_message("You must be a member of the tournament server.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("Bots cannot join the substitute pool.", ephemeral=True)
            return
        try:
            result = await self.db.join_substitute(
                guild_id=self.settings.guild_id, user_id=member.id, display_name=member.display_name,
                now=self.now(), substitute_deadline=self.settings.substitute_deadline
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            "You are already in the substitute pool." if result["outcome"] == "already_active"
            else "You are registered in the substitute pool. During a future game, a team may use at most one substitute.",
            ephemeral=True,
        )
        await self.refresh_panel()

    async def leave_substitute_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.settings.guild_id:
            await interaction.response.send_message("This panel is not active in the configured server.", ephemeral=True)
            return
        result = await self.db.leave_substitute(guild_id=self.settings.guild_id, user_id=interaction.user.id, now=self.now())
        await interaction.response.send_message(
            "You have left the substitute pool." if result["outcome"] == "left" else "You are not currently in the substitute pool.",
            ephemeral=True,
        )
        await self.refresh_panel()

    def is_organizer(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.settings.organizer_user_ids:
            return True
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        return bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))

    async def on_ready(self) -> None:
        guild = self.configured_guild()
        await self.setup_registration_surface(guild)
        pending_teams = await self.db.pending_teams(self.settings.guild_id)
        for team in pending_teams:
            team_id = int(team["id"])
            if team_id not in self._confirmation_views:
                self.add_view(ConfirmationView(self, team_id))
                self._confirmation_views.add(team_id)
            await self.send_confirmation_dms(team, retry_unsent=True, notify_on_failure=False)
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        except discord.HTTPException:
            log.exception("command_sync_failed", extra={"event": "command_sync_failed", "guild_id": guild.id})
        if not self._ready_once:
            self._ready_once = True
            self._expiry_task = asyncio.create_task(self.expiry_loop())
        log.info("bot_ready", extra={"event": "bot_ready", "guild_id": guild.id})

    async def expiry_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                expired = await self.db.expire_pending(now=self.now())
                for team in expired:
                    await self.notify_registrar(
                        team,
                        f"Team **{team['team_name']}** was not confirmed before its expiration window and remains unconfirmed.",
                    )
                if expired:
                    await self.refresh_panel()
            except Exception:
                log.exception("confirmation_expiry_failed", extra={"event": "confirmation_expiry_failed"})
            await asyncio.sleep(60)

    async def close(self) -> None:
        if self._expiry_task:
            self._expiry_task.cancel()
            await asyncio.gather(self._expiry_task, return_exceptions=True)
        await self.db.close()
        await super().close()


def install_commands(bot: TournamentBot) -> None:
    @bot.tree.command(name="tourney-teams", description="List tournament team IDs and statuses (organizers only).")
    async def tourney_teams(interaction: discord.Interaction) -> None:
        if interaction.guild_id != bot.settings.guild_id:
            await interaction.response.send_message("Use this command in the tournament server.", ephemeral=True)
            return
        if not bot.is_organizer(interaction):
            await interaction.response.send_message("Only authorized tournament organizers can view team controls.", ephemeral=True)
            return
        teams = await bot.db.list_teams(bot.settings.guild_id)
        if not teams:
            await interaction.response.send_message("No tournament teams have been submitted.", ephemeral=True)
            return
        lines = [f"`{team['id']}` — **{team['team_name']}** — {team['status']}" for team in teams]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @bot.tree.command(name="tourney-withdraw", description="Withdraw a tournament team (organizers only).")
    @app_commands.describe(team_id="The numeric tournament team ID")
    async def tourney_withdraw(interaction: discord.Interaction, team_id: int) -> None:
        if interaction.guild_id != bot.settings.guild_id:
            await interaction.response.send_message("Use this command in the tournament server.", ephemeral=True)
            return
        if not bot.is_organizer(interaction):
            await interaction.response.send_message("Only Lynx or an authorized tournament organizer can withdraw teams.", ephemeral=True)
            return
        result = await bot.db.withdraw_team(
            guild_id=bot.settings.guild_id, team_id=team_id, actor_user_id=interaction.user.id, now=bot.now()
        )
        if result["outcome"] == "not_found":
            await interaction.response.send_message("That team was not found in this tournament.", ephemeral=True)
            return
        if result["outcome"] == "already_withdrawn":
            await interaction.response.send_message("That team was already marked withdrawn.", ephemeral=True)
            return
        await interaction.response.send_message("The team is marked withdrawn and its slot is free.", ephemeral=True)
        team = result.get("team")
        if team:
            for user_id in result.get("member_ids", []):
                try:
                    user = await bot.fetch_user(int(user_id))
                    await user.send(
                        f"Team **{team['team_name']}** was withdrawn by a tournament organizer. "
                        "You may join the substitute pool again if you are not on another confirmed team.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                    log.warning("withdrawal_member_notification_failed", extra={"event": "withdrawal_member_notification_failed", "team_id": team_id})
            await bot.notify_registrar(team, f"Team **{team['team_name']}** has been marked withdrawn by an organizer.")
        await bot.refresh_panel()


async def run() -> None:
    load_dotenv()
    configure_logging()
    settings = Settings.from_env()
    bot = TournamentBot(settings)
    install_commands(bot)
    try:
        await bot.start(settings.token)
    finally:
        await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(0)

