import os
from pathlib import Path

import asyncpg
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
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID"))

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.messages = True
intents.members = True

client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)
db_pool: asyncpg.Pool = None


# ── Database helpers ─────────────────────────────────────────────────────────

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS starboard_posts (
                message_id BIGINT NOT NULL,
                author_id  BIGINT NOT NULL,
                emoji_type TEXT NOT NULL CHECK (emoji_type IN ('gold', 'brown')),
                reaction_count INT NOT NULL DEFAULT 0,
                PRIMARY KEY (message_id, emoji_type)
            );
        """)


async def upsert_post(message_id: int, author_id: int, emoji_type: str, count: int):
    """Insert or update a starboard post's reaction count."""
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO starboard_posts (message_id, author_id, emoji_type, reaction_count)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (message_id, emoji_type)
            DO UPDATE SET reaction_count = $4;
        """, message_id, author_id, emoji_type, count)


async def delete_post(message_id: int, emoji_type: str):
    """Remove a post from the starboard tracking."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM starboard_posts WHERE message_id = $1 AND emoji_type = $2;",
            message_id, emoji_type,
        )


async def get_leaderboard(emoji_type: str):
    """Return leaderboard rows: [(author_id, posts, total_emojis), ...]"""
    async with db_pool.acquire() as conn:
        return await conn.fetch("""
            SELECT author_id,
                   COUNT(*)::int           AS posts,
                   SUM(reaction_count)::int AS total_emojis
            FROM starboard_posts
            WHERE emoji_type = $1
            GROUP BY author_id
            ORDER BY total_emojis DESC;
        """, emoji_type)


async def get_ratio_leaderboard():
    """Return ratio rows for users on BOTH boards.

    Ratio = (avg gold per post) / (avg brown per post).
    Only includes users with at least one post on each board.
    """
    async with db_pool.acquire() as conn:
        return await conn.fetch("""
            SELECT
                g.author_id,
                g.avg_gold,
                b.avg_brown,
                (g.avg_gold / b.avg_brown) AS ratio
            FROM (
                SELECT author_id,
                       AVG(reaction_count)::float AS avg_gold
                FROM starboard_posts WHERE emoji_type = 'gold'
                GROUP BY author_id
            ) g
            JOIN (
                SELECT author_id,
                       AVG(reaction_count)::float AS avg_brown
                FROM starboard_posts WHERE emoji_type = 'brown'
                GROUP BY author_id
            ) b ON g.author_id = b.author_id
            ORDER BY ratio DESC;
        """)


# ── Discord helpers ──────────────────────────────────────────────────────────

async def create_starboard_embeds(message: discord.Message):
    """Create embed(s) - returns a list with replied-to message first if applicable"""

    embeds = []

    # If this message was a reply, create an embed for the replied-to message first
    if message.reference and message.reference.resolved:
        replied_to = message.reference.resolved
        replied_content = replied_to.system_content or None

        replied_embed = discord.Embed(
            description=replied_content,
            color=discord.Color.greyple(),
            timestamp=replied_to.created_at,
        )

        replied_embed.set_author(
            name=replied_to.author.display_name, icon_url=replied_to.author.display_avatar.url
        )

        if replied_to.attachments:
            attachment = replied_to.attachments[0]
            if attachment.content_type and attachment.content_type.startswith("image"):
                replied_embed.set_image(url=attachment.url)

        embeds.append(replied_embed)

    content = message.system_content or None

    embed = discord.Embed(
        description=content, color=discord.Color.gold(), timestamp=message.created_at
    )

    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)

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


# ── Leaderboard rendering ───────────────────────────────────────────────────

async def update_leaderboard():
    """Rebuild and post the leaderboard from the database."""

    leaderboard_channel = client.get_channel(LEADERBOARD_CHANNEL_ID)
    leaderboard_message = await leaderboard_channel.fetch_message(LEADERBOARD_MESSAGE_ID)

    guild = client.get_guild(GUILD)
    gold_emoji = discord.utils.get(guild.emojis, id=GOLD)
    brown_emoji = discord.utils.get(guild.emojis, id=BROWN)

    # ── Gold embed ───────────────────────────────────────────────────────
    gold_rows = await get_leaderboard("gold")
    gold_embed = discord.Embed(
        title=f"{gold_emoji} Parkour Master Board",
        color=discord.Color.gold(),
    )

    if gold_rows:
        table = "```\n"
        table += f"{'#':<4}{'User':<18}{'Posts':<7}{'Total':<7}\n"
        table += "-" * 36 + "\n"
        for rank, row in enumerate(gold_rows, 1):
            try:
                member = await guild.fetch_member(row["author_id"])
                username = member.display_name[:16]
            except (discord.errors.NotFound, discord.errors.HTTPException):
                continue
            table += f"{rank:<4}{username:<18}{row['posts']:<7}{row['total_emojis']:<7}\n"
        table += "```"
        gold_embed.description = table
    else:
        gold_embed.description = "*No entries yet!*"

    # ── Brown embed ──────────────────────────────────────────────────────
    brown_rows = await get_leaderboard("brown")
    brown_embed = discord.Embed(
        title=f"{brown_emoji} Parkour Noob Board",
        color=0x8B4513,
    )

    if brown_rows:
        table = "```\n"
        table += f"{'#':<4}{'User':<18}{'Posts':<7}{'Total':<7}\n"
        table += "-" * 36 + "\n"
        for rank, row in enumerate(brown_rows, 1):
            try:
                member = await guild.fetch_member(row["author_id"])
                username = member.display_name[:16]
            except (discord.errors.NotFound, discord.errors.HTTPException):
                continue
            table += f"{rank:<4}{username:<18}{row['posts']:<7}{row['total_emojis']:<7}\n"
        table += "```"
        brown_embed.description = table
    else:
        brown_embed.description = "*No entries yet!*"

    # ── Ratio embed ──────────────────────────────────────────────────────
    ratio_rows = await get_ratio_leaderboard()
    ratio_embed = discord.Embed(
        title=f"{gold_emoji}/{brown_emoji} Master-to-Noob Ratio",
        color=discord.Color.blue(),
    )

    if ratio_rows:
        table = "```\n"
        table += f"{'#':<4}{'User':<18}{'Ratio':<7}\n"
        table += "-" * 29 + "\n"
        for rank, row in enumerate(ratio_rows, 1):
            try:
                member = await guild.fetch_member(
                    row["author_id"]
                )
                username = member.display_name[:16]
            except (
                discord.errors.NotFound,
                discord.errors.HTTPException,
            ):
                continue
            table += (
                f"{rank:<4}{username:<18}"
                f"{row['ratio']:<7.2f}\n"
            )
        table += "```"
        ratio_embed.description = table
    else:
        ratio_embed.description = "*Need entries on both boards!*"

    await leaderboard_message.edit(
        content="# Leaderboard",
        embeds=[gold_embed, brown_embed, ratio_embed],
    )


# ── Slash commands ───────────────────────────────────────────────────────────

@tree.command(
    name="reset",
    description="Wipe the database and clear all starboard channels",
    guild=discord.Object(id=GUILD),
)
async def reset_command(interaction: discord.Interaction):
    # Check for admin role
    has_role = any(
        r.id == ADMIN_ROLE_ID for r in interaction.user.roles
    )
    if not has_role:
        await interaction.response.send_message(
            "You need the admin role to use this command.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    # Wipe the database
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM starboard_posts;")

    # Purge bot messages from both starboard channels
    gold_channel = client.get_channel(GOLD_BOARD_ID)
    brown_channel = client.get_channel(BROWN_BOARD_ID)

    for channel in [gold_channel, brown_channel]:
        async for msg in channel.history(limit=None):
            if msg.author == client.user:
                await msg.delete()

    # Reset the leaderboard message
    await update_leaderboard()

    await interaction.followup.send(
        "All boards have been wiped.", ephemeral=True
    )


# ── Events ───────────────────────────────────────────────────────────────────

@client.event
async def on_ready():
    await init_db()
    await tree.sync(guild=discord.Object(id=GUILD))
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
        emoji_type = "gold" if emoji.id == GOLD else "brown"
        existing_message = await find_existing_starboard_message(channel_to_post, message_link)

        if existing_message and existing_message.author == client.user:
            new_content = f"{emoji} **{count}** | {message_link}"
            await existing_message.edit(content=new_content)
        else:
            embeds = await create_starboard_embeds(message)
            await channel_to_post.send(
                f"{emoji} **{count}** | {message_link}",
                embeds=embeds,
            )

        await upsert_post(payload.message_id, message.author.id, emoji_type, count)
        await update_leaderboard()


@client.event
async def on_raw_reaction_remove(payload):
    if payload.channel_id in [GOLD_BOARD_ID, BROWN_BOARD_ID]:
        return

    # Only handle gold/brown emoji removals
    if not hasattr(payload.emoji, "id"):
        return
    if payload.emoji.id not in [GOLD, BROWN]:
        return

    channel = client.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    message_link = get_message_link(payload)

    guild = client.get_guild(GUILD)
    if payload.emoji.id == GOLD:
        board_channel = client.get_channel(GOLD_BOARD_ID)
        emoji = discord.utils.get(guild.emojis, id=GOLD)
        emoji_type = "gold"
    else:
        board_channel = client.get_channel(BROWN_BOARD_ID)
        emoji = discord.utils.get(guild.emojis, id=BROWN)
        emoji_type = "brown"

    # Find current count for this emoji (0 if fully removed)
    count = 0
    for reaction in message.reactions:
        if hasattr(reaction.emoji, "id"):
            if reaction.emoji.id == payload.emoji.id:
                count = reaction.count
                break

    existing = await find_existing_starboard_message(
        board_channel, message_link
    )

    if existing and existing.author == client.user:
        if count < MIN_COUNT:
            await existing.delete()
            await delete_post(payload.message_id, emoji_type)
        else:
            new_content = (
                f"{emoji} **{count}** | {message_link}"
            )
            await existing.edit(content=new_content)
            await upsert_post(
                payload.message_id,
                message.author.id,
                emoji_type,
                count,
            )

        await update_leaderboard()


client.run(TOKEN)
