import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = int(os.getenv('GUILD_ID', 876523812559159336))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    try:
        # Register all commands globally
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands globally.")
        # Optionally, also sync to test guild for instant updates
        test_guild = discord.Object(id=GUILD_ID)
        test_synced = await bot.tree.sync(guild=test_guild)
        print(f"Synced {len(test_synced)} commands to the test guild.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    # Set streaming presence
    streaming = discord.Streaming(
        name="Music",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    await bot.change_presence(activity=streaming)
# Load cogs
async def setup():
    await bot.load_extension("cogs.music")
    await bot.load_extension("cogs.admin")
    await bot.load_extension("cogs.help")

if __name__ == "__main__":
    import asyncio
    asyncio.run(setup())
    bot.run(TOKEN)