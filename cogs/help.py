import discord
from discord.ext import commands
from discord import app_commands, Embed
from utils.pagination import send_paginated_embed
import os

GUILD_ID = int(os.getenv("GUILD_ID", "876523812559159336"))

class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name="help", description="Show help for all commands.")
    async def help_command(self, interaction: discord.Interaction):
        categories = [
            {
                "name": "Music",
                "commands": [
                    ("/join", "Join your voice channel."),
                    ("/play <song/playlist>", "Play a song, YouTube playlist link, or your saved playlist."),
                    ("/queue", "Show the current song queue with pagination."),
                    ("/skip", "Skip the currently playing song."),
                    ("/pause", "Pause the current song."),
                    ("/resume", "Resume the paused song."),
                    ("/stop", "Stop playback and clear the queue."),
                    ("/volume [0-100]", "Show or set the volume."),
                    ("/shuffle", "Shuffle the current song queue."),
                    ("/playlist", "Manage your playlists (create, delete, add, remove, view)."),
                    ("/loop <mode>", "Set loop mode: song (repeat current), queue (repeat queue), or off."),
                ]
            },
            {
                "name": "Admin",
                "commands": [
                    ("!sync", "Sync all slash commands to the test guild (admin only)."),
                ]
            }
        ]
        pages = []
        for cat in categories:
            desc = f"**{cat['name']} Commands**\n\n"
            for cmd, cmd_desc in cat["commands"]:
                desc += f"**{cmd}**\n{cmd_desc}\n\n"
            pages.append(desc.strip())  # <-- FIXED
        await send_paginated_embed(
            interaction,
            pages,
            title="Bot Help",
            per_page=1,
            ephemeral=True
        )

async def setup(bot):
    await bot.add_cog(Help(bot))