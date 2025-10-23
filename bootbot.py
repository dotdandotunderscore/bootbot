# This example requires the 'message_content' intent.

import os

import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
GUILD = int(os.getenv("GUILD"))
GOLD = int(os.getenv("GOLD"))
BROWN = int(os.getenv("BROWN"))
GOLD_BOARD_ID = int(os.getenv("GOLD_BOARD_ID"))
BROWN_BOARD_ID = int(os.getenv("BROWN_BOARD_ID"))

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.messages = True
intents.members = True

client = discord.Client(intents=intents)


async def create_starboard_embeds(message: discord.Message):
    """Create embed(s) - returns a list with replied-to message first if applicable"""

    embeds = []

    # If this message was a reply, create an embed for the replied-to message first
    if message.reference and message.reference.resolved:
        replied_to = message.reference.resolved
        replied_content = replied_to.system_content or None

        replied_embed = discord.Embed(
            description=replied_content,
            color=discord.Color.greyple(),  # Different color to distinguish
            timestamp=replied_to.created_at,
        )

        replied_embed.set_author(
            name=replied_to.author.display_name, icon_url=replied_to.author.display_avatar.url
        )

        # Add image if present
        if replied_to.attachments:
            attachment = replied_to.attachments[0]
            if attachment.content_type and attachment.content_type.startswith("image"):
                replied_embed.set_image(url=attachment.url)

        embeds.append(replied_embed)

    # Create the embed for the actual message
    content = message.system_content or None

    embed = discord.Embed(
        description=content, color=discord.Color.gold(), timestamp=message.created_at
    )

    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)

    # Add image if present
    if message.attachments:
        attachment = message.attachments[0]
        if attachment.content_type and attachment.content_type.startswith("image"):
            embed.set_image(url=attachment.url)

    embeds.append(embed)

    return embeds


def get_message_link(payload):
    """Build Discord message link from payload"""
    return (
        "https://discord.com/channels/"
        + f"{payload.guild_id}/{payload.channel_id}/{payload.message_id}"
    )


def get_starboard_updates(message, min_count=3):
    """Check reactions and return list of (channel, emoji, count) tuples for starboard updates"""
    updates = []
    for reaction in message.reactions:
        if hasattr(reaction.emoji, "id"):
            if reaction.emoji.id == GOLD and reaction.count >= min_count:
                updates.append(
                    (
                        client.get_channel(GOLD_BOARD_ID),
                        discord.utils.get(client.get_guild(GUILD).emojis, id=GOLD),
                        reaction.count,
                    )
                )
            elif reaction.emoji.id == BROWN and reaction.count >= min_count:
                updates.append(
                    (
                        client.get_channel(BROWN_BOARD_ID),
                        discord.utils.get(client.get_guild(GUILD).emojis, id=BROWN),
                        reaction.count,
                    )
                )
    return updates


async def find_existing_starboard_message(channel, message_link, limit=100):
    """Search for existing starboard message containing the message link"""
    async for starboard_msg in channel.history(limit=limit):
        if message_link in starboard_msg.content:
            return starboard_msg
    return None


@client.event
async def on_ready():
    print(f"We have logged in as {client.user}")


@client.event
async def on_raw_reaction_add(payload):
    if payload.channel_id in [GOLD_BOARD_ID, BROWN_BOARD_ID]:
        return

    channel = client.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    message_link = get_message_link(payload)
    updates = get_starboard_updates(message, min_count=3)

    for channel_to_post, emoji, count in updates:
        existing_message = await find_existing_starboard_message(channel_to_post, message_link)

        if existing_message and existing_message.author == client.user:
            # Update existing message with new count
            new_content = f"{emoji} **{count}** | {message_link}"
            await existing_message.edit(content=new_content)
        else:
            # Post new message to starboard
            embeds = await create_starboard_embeds(message)
            await channel_to_post.send(f"{emoji} **{count}** | {message_link}", embeds=embeds)


@client.event
async def on_raw_reaction_remove(payload):
    if payload.channel_id in [GOLD_BOARD_ID, BROWN_BOARD_ID]:
        return

    channel = client.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    message_link = get_message_link(payload)
    updates = get_starboard_updates(message, min_count=0)

    for channel_to_post, emoji, count in updates:
        existing_message = await find_existing_starboard_message(channel_to_post, message_link)

        if existing_message and existing_message.author == client.user:
            if count < 3:
                # Remove message from starboard if count drops below 3
                await existing_message.delete()
            else:
                # Update existing message with new count
                new_content = f"{emoji} **{count}** | {message_link}"
                await existing_message.edit(content=new_content)


client.run(TOKEN)
