from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import discord
import pytest

from bot import TournamentBot


def surface(channel_id, channel=None):
    bot = SimpleNamespace(settings=SimpleNamespace(registration_channel_id=channel_id))
    guild = SimpleNamespace(
        id=1379702029391364166,
        me=object(),
        get_channel=Mock(return_value=channel),
        create_category=AsyncMock(),
        create_text_channel=AsyncMock(),
    )
    return bot, guild


@pytest.mark.asyncio
async def test_existing_channel_keeps_category_without_manage_channels():
    channel = Mock(spec=discord.TextChannel)
    channel.id = 1572745531313098844
    channel.category = SimpleNamespace(id=1519433631121149992, name="VGNA Premier League")
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True, send_messages=True, embed_links=True,
        read_message_history=True, manage_channels=False,
    )
    bot, guild = surface(channel.id, channel)
    assert await TournamentBot.ensure_registration_channel(bot, guild) is channel
    assert channel.category.id == 1519433631121149992
    guild.create_category.assert_not_called()
    guild.create_text_channel.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", [None, 1572745531313098844])
async def test_missing_destination_fails_without_creating_anything(channel_id):
    bot, guild = surface(channel_id)
    with pytest.raises(RuntimeError, match="TOURNAMENT_REGISTRATION_CHANNEL_ID"):
        await TournamentBot.ensure_registration_channel(bot, guild)
    guild.create_category.assert_not_called()
    guild.create_text_channel.assert_not_called()
