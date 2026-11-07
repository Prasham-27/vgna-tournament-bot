"""Public registration panel rendering."""

from __future__ import annotations

from datetime import datetime

import discord

from .config import Settings


def format_deadline(value: datetime) -> str:
    return value.strftime("%B %-d, %Y at %-I:%M:%S %p %Z")


def registration_embed(snapshot: dict, settings: Settings) -> discord.Embed:
    embed = discord.Embed(
        title="VGNA Tournament Registration",
        description=(
            "Register a five-player team or join the substitute pool below. "
            "A team is confirmed only when all five selected members approve in DM.\n\n"
            "**If your team needs to withdraw, please DM Lynx.**"
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Teams",
        value=(
            f"Confirmed: **{snapshot['confirmed_count']} / {settings.max_teams}**\n"
            f"Pending confirmation: **{snapshot['pending_count']}**"
        ),
        inline=True,
    )
    embed.add_field(name="Substitute pool", value=f"**{snapshot['substitute_count']}** registered", inline=True)
    embed.add_field(
        name="Deadlines",
        value=(
            f"Team registration: **{format_deadline(settings.team_deadline)}**\n"
            f"Substitute pool: **{format_deadline(settings.substitute_deadline)}**"
        ),
        inline=False,
    )
    if snapshot["teams"]:
        for team in snapshot["teams"]:
            names = ", ".join(str(member["display_name"]) for member in team["members"])
            embed.add_field(name=f"✅ {team['team_name']}", value=names or "(members unavailable)", inline=False)
    else:
        embed.add_field(name="Confirmed teams", value="No teams are confirmed yet.", inline=False)
    embed.set_footer(text="During a future game, a team may use at most one substitute.")
    return embed

