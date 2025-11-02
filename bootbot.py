import os
from pathlib import Path

import discord

if Path(".env").exists():
    from dotenv import load_dotenv
    load_dotenv()

TOKEN = os.getenv("TOKEN")
GUILD = int(os.getenv("GUILD"))
GOLD = int(os.getenv("GOLD"))
BROWN = int(os.getenv("BROWN"))
GOLD_BOARD_ID = int(os.getenv("GOLD_BOARD_ID"))
BROWN_BOARD_ID = int(os.getenv("BROWN_BOARD_ID"))
MIN_COUNT = int(os.getenv("MIN_COUNT", 3))
LEADERBOARD_CHANNEL_ID = int(os.getenv("LEADERBOARD_CHANNEL_ID"))
LEADERBOARD_MESSAGE_ID = int(os.getenv("LEADERBOARD_MESSAGE_ID"))

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


def get_starboard_updates(message, min_count=MIN_COUNT):
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


def parse_leaderboard_embed(embed):
    """Parse an embed to extract user stats from the table"""
    stats = {}
    if not embed.description or embed.description == "*No entries yet!*":
        return stats

    # Parse the table in the embed description
    lines = embed.description.strip('`').split('\n')
    for line in lines[2:]:  # Skip header and separator
        line = line.strip()
        if line and line != '```' and not line.startswith('-'):
            try:
                # Split by whitespace, format: rank username posts total
                parts = line.split()
                if len(parts) >= 4:
                    total = int(parts[-1])
                    posts = int(parts[-2])
                    # Username is everything between rank and the last two numbers
                    username = ' '.join(parts[1:-2])

                    stats[username] = {'posts': posts, 'total_emojis': total}
            except (ValueError, IndexError):
                continue
    return stats


async def update_leaderboard(user_id, emoji_id, new_count, old_count=None, is_new_post=False):
    """Update leaderboard message incrementally based on a change to the starboard.

    Args:
        user_id: The author of the message that was added/updated/removed
        emoji_id: GOLD or BROWN emoji ID to know which board changed
        new_count: Current reaction count (0 means removed from board)
        old_count: Previous reaction count (for updates)
        is_new_post: True if this is a new post being added to the board
    """

    # Get the leaderboard channel and message
    leaderboard_channel = client.get_channel(LEADERBOARD_CHANNEL_ID)
    leaderboard_message = await leaderboard_channel.fetch_message(LEADERBOARD_MESSAGE_ID)

    guild = client.get_guild(GUILD)

    # Parse existing leaderboard embeds to get current stats
    gold_stats = {}  # user_id -> {'posts': count, 'total_emojis': count}
    brown_stats = {}

    if leaderboard_message.embeds:
        # Map existing stats from embeds (keyed by username)
        if len(leaderboard_message.embeds) > 0:
            gold_stats_by_name = parse_leaderboard_embed(
                leaderboard_message.embeds[0]
            )
        else:
            gold_stats_by_name = {}

        if len(leaderboard_message.embeds) > 1:
            brown_stats_by_name = parse_leaderboard_embed(
                leaderboard_message.embeds[1]
            )
        else:
            brown_stats_by_name = {}

        # Convert username-based stats to user_id-based stats
        for member in guild.members:
            username = member.display_name[:16]
            if username in gold_stats_by_name:
                gold_stats[member.id] = gold_stats_by_name[username]
            if username in brown_stats_by_name:
                brown_stats[member.id] = brown_stats_by_name[username]

    # Determine which board to update
    target_stats = gold_stats if emoji_id == GOLD else brown_stats

    # Initialize user stats if they don't exist
    if user_id not in target_stats:
        target_stats[user_id] = {'posts': 0, 'total_emojis': 0}

    # Apply the incremental update
    if is_new_post:
        # New post added to board (just hit MIN_COUNT threshold)
        target_stats[user_id]['posts'] += 1
        target_stats[user_id]['total_emojis'] += new_count
    elif new_count == 0:
        # Post removed from board (dropped below MIN_COUNT)
        target_stats[user_id]['posts'] -= 1
        target_stats[user_id]['total_emojis'] -= old_count if old_count else 0
        # Remove user from stats if they have no posts
        if target_stats[user_id]['posts'] <= 0:
            del target_stats[user_id]
    else:
        # Post updated (reaction added or removed, but still on board)
        delta = new_count - (old_count if old_count else new_count - 1)
        target_stats[user_id]['total_emojis'] += delta

    # Get guild and emojis
    gold_emoji = discord.utils.get(guild.emojis, id=GOLD)
    brown_emoji = discord.utils.get(guild.emojis, id=BROWN)

    # Sort by total emojis (descending)
    gold_sorted = sorted(gold_stats.items(), key=lambda x: x[1]['total_emojis'], reverse=True)
    brown_sorted = sorted(brown_stats.items(), key=lambda x: x[1]['total_emojis'], reverse=True)

    # Build embed for gold board
    gold_embed = discord.Embed(
        title=f"{gold_emoji} Parkour Master Board",
        color=discord.Color.gold()
    )

    if gold_sorted:
        # Build table as field value
        table = "```\n"
        table += f"{'#':<4}{'User':<18}{'Posts':<7}{'Total':<7}\n"
        table += "-" * 36 + "\n"

        for rank, (uid, stats) in enumerate(gold_sorted, 1):
            try:
                user = await guild.fetch_member(uid)
                username = user.display_name[:16]
                table += (
                    f"{rank:<4}{username:<18}"
                    f"{stats['posts']:<7}{stats['total_emojis']:<7}\n"
                )
            except (discord.errors.NotFound, discord.errors.HTTPException):
                continue

        table += "```"
        gold_embed.description = table
    else:
        gold_embed.description = "*No entries yet!*"

    # Build embed for brown board
    brown_embed = discord.Embed(
        title=f"{brown_emoji} Parkour Noob Board",
        color=0x8B4513  # Brown color
    )

    if brown_sorted:
        table = "```\n"
        table += f"{'#':<4}{'User':<18}{'Posts':<7}{'Total':<7}\n"
        table += "-" * 36 + "\n"

        for rank, (uid, stats) in enumerate(brown_sorted, 1):
            try:
                user = await guild.fetch_member(uid)
                username = user.display_name[:16]
                table += (
                    f"{rank:<4}{username:<18}"
                    f"{stats['posts']:<7}{stats['total_emojis']:<7}\n"
                )
            except (discord.errors.NotFound, discord.errors.HTTPException):
                continue

        table += "```"
        brown_embed.description = table
    else:
        brown_embed.description = "*No entries yet!*"

    # Update the leaderboard message with embeds
    await leaderboard_message.edit(content="# Leaderboard", embeds=[gold_embed, brown_embed])


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
    updates = get_starboard_updates(message, min_count=MIN_COUNT)

    if payload.message_author_id == payload.user_id and payload.emoji.id in [GOLD, BROWN]:
        await message.remove_reaction(payload.emoji, client.get_user(payload.user_id))
        return

    for channel_to_post, emoji, count in updates:
        existing_message = await find_existing_starboard_message(channel_to_post, message_link)

        if existing_message and existing_message.author == client.user:
            # Update existing message with new count
            # Extract old count from existing message
            old_count = int(existing_message.content.split('**')[1])
            new_content = f"{emoji} **{count}** | {message_link}"
            await existing_message.edit(content=new_content)
            await update_leaderboard(
                message.author.id, emoji.id, count,
                old_count=old_count, is_new_post=False
            )
        else:
            # Post new message to starboard
            embeds = await create_starboard_embeds(message)
            await channel_to_post.send(
                f"{emoji} **{count}** | {message_link}",
                embeds=embeds
            )
            await update_leaderboard(
                message.author.id, emoji.id, count, is_new_post=True
            )


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
            # Extract old count from existing message
            old_count = int(existing_message.content.split('**')[1])

            if count < MIN_COUNT:
                # Remove message from starboard if count drops below MIN_COUNT
                await existing_message.delete()
                await update_leaderboard(
                    message.author.id, emoji.id, 0,
                    old_count=old_count, is_new_post=False
                )
            else:
                # Update existing message with new count
                new_content = f"{emoji} **{count}** | {message_link}"
                await existing_message.edit(content=new_content)
                await update_leaderboard(
                    message.author.id, emoji.id, count,
                    old_count=old_count, is_new_post=False
                )


client.run(TOKEN)
