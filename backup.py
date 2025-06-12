import os
from typing import Optional
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
import asyncio
from collections import deque
import json
from difflib import get_close_matches
from typing import Optional
from discord import Embed, ui, Interaction
import re
import random

# =========================
# === ENV & CONSTANTS =====
# =========================

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

GUILD_ID = 876523812559159336
ALLOWED_USERS = {"569616893095313408", "694545739883348038"}

# Persistent storage setup
PERSISTENT_DIR = "persistent"
os.makedirs(PERSISTENT_DIR, exist_ok=True)
VOLUME_FILE = os.path.join(PERSISTENT_DIR, "guild_volumes.json")
PLAYLISTS_FILE = os.path.join(PERSISTENT_DIR, "playlists.json")

# =========================
# === GLOBAL VARIABLES ====
# =========================

SONG_QUEUES = {}         # {guild_id: deque([(audio_url, title), ...])}
GUILD_VOLUMES = {}       # {guild_id: volume_float}
PLAYLISTS = {}           # {user_id: {playlist_name: [{"query": ..., "title": ...}, ...]}}

# =========================
# === PERSISTENCE UTILS ===
# =========================

def save_volumes():
    with open(VOLUME_FILE, "w") as f:
        json.dump(GUILD_VOLUMES, f)

def load_volumes():
    global GUILD_VOLUMES
    try:
        with open(VOLUME_FILE, "r") as f:
            GUILD_VOLUMES = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        GUILD_VOLUMES = {}

def save_playlists(playlists):
    with open(PLAYLISTS_FILE, "w") as f:
        json.dump(playlists, f, indent=2)

def load_playlists():
    try:
        with open(PLAYLISTS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

# Load persistent data at startup
PLAYLISTS = load_playlists()
load_volumes()

# =========================
# === UTILITY FUNCTIONS ===
# =========================

def is_playlist_user(user_id):
    """Check if a user is allowed to manage playlists."""
    return str(user_id) in ALLOWED_USERS

async def search_ytdlp_async(query, ydl_opts):
    """Run yt-dlp search asynchronously."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

async def get_song_metadata(song_query: str) -> Optional[str]:
    """Search for a song using yt-dlp and return its title (no download)."""
    ydl_options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "extract_flat": "in_playlist",  # Only fetch metadata, do not download
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
    }
    results = await search_ytdlp_async("ytsearch1:" + song_query, ydl_options)
    tracks = results.get('entries', [])
    if not tracks:
        return None
    first_track = tracks[0]
    title = first_track.get("title", song_query)
    return title

def is_youtube_playlist_url(query: str) -> bool:
    """Check if the query is a YouTube playlist URL."""
    # Basic check for YouTube playlist URL
    return bool(re.search(r"(youtube\.com|youtu\.be).*(list=)", query))

async def get_playlist_entries(playlist_url: str):
    """Return a list of dicts with 'title' and 'url' for each entry in a YouTube playlist."""
    ydl_options = {
        "extract_flat": True,
        "quiet": True,
        "skip_download": True,
        "force_generic_extractor": False,
    }
    results = await search_ytdlp_async(playlist_url, ydl_options)
    entries = results.get("entries", [])
    # Filter out non-video entries and return title/url
    return [
        {"title": entry.get("title", "Unknown Title"), "url": entry.get("url")}
        for entry in entries if entry.get("url")
    ]

# =========================
# === DISCORD BOT SETUP ===
# =========================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# =========================
# === BOT EVENTS ==========
# =========================

@bot.event
async def on_ready():
    print('------')
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    test_guild = discord.Object(id=GUILD_ID)
    try:
        synced = await bot.tree.sync(guild=test_guild)
        print(f"Synced {len(synced)} commands to the test guild.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.event
async def on_disconnect():
    """Save persistent data on disconnect."""
    save_volumes()
    save_playlists(PLAYLISTS)

@bot.event
async def on_shutdown():
    """Save persistent data on shutdown (discord.py 2.3+)."""
    save_volumes()
    save_playlists(PLAYLISTS)

# =========================
# === PAGINATION HELPER ===
# =========================

class PaginationView(ui.View):
    def __init__(self, pages, title, ephemeral=False):  # Default to non-ephemeral
        super().__init__(timeout=120)
        self.pages = pages
        self.title = title
        self.page = 0
        self.ephemeral = ephemeral
        self.message = None  # Store the message object

    def update_buttons(self):
        self.clear_items()
        prev_disabled = self.page == 0
        next_disabled = self.page == len(self.pages) - 1
        self.add_item(self.previous)
        self.add_item(self.next)
        self.previous.disabled = prev_disabled
        self.next.disabled = next_disabled

    async def send_or_edit(self, interaction: Interaction):
        embed = Embed(
            title=f"{self.title} (Page {self.page+1}/{len(self.pages)})",
            description="\n".join(self.pages[self.page])
        )
        self.update_buttons()
        if self.message is None:
            # First response: send the message and store it
            await interaction.response.send_message(embed=embed, view=self, ephemeral=self.ephemeral)
            self.message = await interaction.original_response()
        else:
            # Always edit the stored message
            await self.message.edit(embed=embed, view=self)

    @ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="prev", disabled=True)
    async def previous(self, interaction: Interaction, button: ui.Button):
        if self.page > 0:
            self.page -= 1
            await self.send_or_edit(interaction)
            await interaction.response.defer()  # Acknowledge the button press

    @ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="next", disabled=True)
    async def next(self, interaction: Interaction, button: ui.Button):
        if self.page < len(self.pages) - 1:
            self.page += 1
            await self.send_or_edit(interaction)
            await interaction.response.defer()  # Acknowledge the button press

async def send_paginated_embed(interaction, items, title, per_page=10, ephemeral=False):  # Default to non-ephemeral
    """Send a paginated embed for long lists with navigation buttons."""
    if not items:
        embed = Embed(title=title, description="No items found.")
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        return
    pages = [items[i:i+per_page] for i in range(0, len(items), per_page)]
    if len(pages) == 1:
        embed = Embed(title=title, description="\n".join(pages[0]))
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
    else:
        view = PaginationView(pages, title, ephemeral=ephemeral)
        await view.send_or_edit(interaction)

# =========================
# === MUSIC COMMANDS ======
# =========================

@bot.tree.command(name='join', description='Join your voice channel.', guild=discord.Object(id=GUILD_ID))
async def join(interaction: discord.Interaction):
    """Join the user's voice channel, unless already playing elsewhere."""
    # Check if bot is already playing music in any voice channel in the guild
    for vc in interaction.guild.voice_channels:
        if vc.guild.voice_client and (vc.guild.voice_client.is_playing() or vc.guild.voice_client.is_paused()):
            embed = Embed(title="Already Playing", description="The bot is already playing music in another channel.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
    if not interaction.user.voice or not interaction.user.voice.channel:
        embed = Embed(title="Not Connected", description="You must be in a voice channel to use this command.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    channel = interaction.user.voice.channel
    await channel.connect()
    embed = Embed(title="Joined", description=f"Joined {channel.mention}.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='play', description='Play a song, YouTube playlist, or your saved playlist.', guild=discord.Object(id=GUILD_ID))
@app_commands.describe(song_or_playlist='Song query, YouTube playlist link, or playlist name')
async def play(interaction: discord.Interaction, song_or_playlist: str):
    """Play a song by search, YouTube playlist link, or a saved playlist."""
    # Check if bot is already playing music in any voice channel in the guild
    for vc in interaction.guild.voice_channels:
        if vc.guild.voice_client and (vc.guild.voice_client.is_playing() or vc.guild.voice_client.is_paused()):
            embed = Embed(title="Already Playing", description="The bot is already playing music in another channel.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
    await interaction.response.defer(ephemeral=True)
    voice_channel = interaction.user.voice.channel
    if voice_channel is None:
        embed = Embed(title="Not Connected", description="You need to be in a voice channel to play music.")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_channel != voice_client.channel:
        await voice_client.move_to(voice_channel)
    guild_id = str(interaction.guild.id)
    user_id = str(interaction.user.id)
    # Check if it's a playlist name for this user
    playlist = PLAYLISTS.get(user_id, {}).get(song_or_playlist)
    if playlist:
        if SONG_QUEUES.get(guild_id) is None:
            SONG_QUEUES[guild_id] = deque()
        if not playlist:
            await interaction.followup.send("Playlist is empty.")
            return

        # Play the first song immediately
        first_entry = playlist[0]
        ydl_options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "youtube_include_dash_manifest": False,
            "youtube_include_hls_manifest": False,
        }
        results = await search_ytdlp_async("ytsearch1:" + first_entry['query'], ydl_options)
        tracks = results.get('entries', [])
        if not tracks:
            await interaction.followup.send(f"Could not find: {first_entry['title']}")
            return
        first_track = tracks[0]
        audio_url = first_track["url"]
        title = first_track.get("title", first_entry['title'])
        SONG_QUEUES[guild_id].append((audio_url, title))

        # Start playing if not already
        if not voice_client.is_playing() and not voice_client.is_paused():
            await play_next_song(voice_client, guild_id, interaction.channel)

        # Download the rest in the background
        async def queue_rest(entries):
            for entry in entries:
                ydl_options = {
                    "format": "bestaudio/best",
                    "noplaylist": True,
                    "youtube_include_dash_manifest": False,
                    "youtube_include_hls_manifest": False,
                }
                results = await search_ytdlp_async("ytsearch1:" + entry['query'], ydl_options)
                tracks = results.get('entries', [])
                if not tracks:
                    continue
                first_track = tracks[0]
                audio_url = first_track["url"]
                title = first_track.get("title", entry['title'])
                SONG_QUEUES[guild_id].append((audio_url, title))

        # Schedule background task for the rest of the playlist
        asyncio.create_task(queue_rest(playlist[1:]))

        await interaction.followup.send(f"Queued playlist '{song_or_playlist}'.")
        return
    # Otherwise, treat as a song or playlist link
    if is_youtube_playlist_url(song_or_playlist):
        entries = await get_playlist_entries(song_or_playlist)
        if not entries:
            await interaction.followup.send("No valid songs found in the playlist link.")
            return
        if SONG_QUEUES.get(guild_id) is None:
            SONG_QUEUES[guild_id] = deque()

        # Play the first song immediately
        first_entry = entries[0]
        ydl_options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "youtube_include_dash_manifest": False,
            "youtube_include_hls_manifest": False,
        }
        results = await search_ytdlp_async(first_entry['url'], ydl_options)
        tracks = results.get('entries', [])
        if not tracks:
            await interaction.followup.send(f"Could not find: {first_entry['title']}")
            return
        first_track = tracks[0]
        audio_url = first_track["url"]
        title = first_track.get("title", first_entry['title'])
        SONG_QUEUES[guild_id].append((audio_url, title))

        # Start playing if not already
        if not voice_client.is_playing() and not voice_client.is_paused():
            await play_next_song(voice_client, guild_id, interaction.channel)

        # Download the rest in the background
        async def queue_rest(entries):
            for entry in entries:
                ydl_options = {
                    "format": "bestaudio/best",
                    "noplaylist": True,
                    "youtube_include_dash_manifest": False,
                    "youtube_include_hls_manifest": False,
                }
                results = await search_ytdlp_async(entry['url'], ydl_options)
                tracks = results.get('entries', [])
                if not tracks:
                    continue
                first_track = tracks[0]
                audio_url = first_track["url"]
                title = first_track.get("title", entry['title'])
                SONG_QUEUES[guild_id].append((audio_url, title))

        # Schedule background task for the rest of the playlist
        asyncio.create_task(queue_rest(entries[1:]))

        await interaction.followup.send(f"Queued playlist from link.")
        return

    # Otherwise, treat as a song query (existing code)
    ydl_options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
    }
    query = "ytsearch1:" + song_or_playlist
    results = await search_ytdlp_async(query, ydl_options)
    tracks = results.get('entries', [])
    if not tracks:
        await interaction.followup.send("No results found.")
        return
    first_track = tracks[0]
    audio_url = first_track["url"]
    title = first_track.get("title", "Unknown Title")
    if SONG_QUEUES.get(guild_id) is None:
        SONG_QUEUES[guild_id] = deque()
    SONG_QUEUES[guild_id].append((audio_url, title))
    if voice_client.is_playing() or voice_client.is_paused():
        await interaction.followup.send(f"Added to queue: {title}")
    else:
        await interaction.followup.send(f"Now playing: {title}")
        await play_next_song(voice_client, guild_id, interaction.channel)

@bot.tree.command(name='queue', description='Show the current song queue with pagination.', guild=discord.Object(id=GUILD_ID))
async def queue(interaction: discord.Interaction):
    """Show the current queue with pagination and now playing."""
    guild_id = str(interaction.guild.id)
    if guild_id not in SONG_QUEUES or not SONG_QUEUES[guild_id]:
        embed = Embed(title="Current Queue", description="The queue is currently empty.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Show now playing if possible
    voice_client = interaction.guild.voice_client
    now_playing = None
    if voice_client and voice_client.is_playing() and voice_client.source:
        # Try to get the title from the current source if possible
        # If you store the current song title elsewhere, use that
        now_playing = getattr(voice_client.source, "title", None)
    if not now_playing and SONG_QUEUES[guild_id]:
        # Fallback: show the first in queue as now playing
        now_playing = SONG_QUEUES[guild_id][0][1]

    lines = []
    if now_playing:
        lines.append(f"**Now Playing:** {now_playing}\n")
    # List the rest of the queue (skip the first if it's now playing)
    queue_lines = [f"{i+1}. {title}" for i, (_, title) in enumerate(SONG_QUEUES[guild_id])]
    lines.extend(queue_lines)
    await send_paginated_embed(interaction, lines, "Current Queue", ephemeral=True)

@bot.tree.command(name='skip', description='Skip the currently playing song.', guild=discord.Object(id=GUILD_ID))
async def skip(interaction: discord.Interaction):
    """Skip the currently playing song."""
    if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("Skipped the current song.")
    else:
        await interaction.response.send_message("No song is currently playing.")

@bot.tree.command(name='pause', description='Pause the current song.', guild=discord.Object(id=GUILD_ID))
async def pause(interaction: discord.Interaction):
    """Pause the current song."""
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        return await interaction.response.send_message("Not connected to a voice channel.")
    if not voice_client.is_playing():
        return await interaction.response.send_message("No song is currently playing.")
    voice_client.pause()
    await interaction.response.send_message("Paused the current song.")

@bot.tree.command(name='resume', description='Resume the paused song.', guild=discord.Object(id=GUILD_ID))
async def resume(interaction: discord.Interaction):
    """Resume the paused song."""
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        return await interaction.response.send_message("Not connected to a voice channel.")
    if not voice_client.is_paused():
        return await interaction.response.send_message("No song is currently paused.")
    voice_client.resume()
    await interaction.response.send_message("Resumed the current song.")

@bot.tree.command(name='stop', description='Stop playback and clear the queue.', guild=discord.Object(id=GUILD_ID))
async def stop(interaction: discord.Interaction):
    """Stop playback and clear the queue."""
    await interaction.response.defer()
    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_connected():
        await interaction.followup.send("Not connected to a voice channel.")
        return
    guild_id_str = str(interaction.guild.id)
    if guild_id_str in SONG_QUEUES:
        SONG_QUEUES[guild_id_str].clear()
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()
    await interaction.followup.send("Stopped the current song and cleared the queue.")
    await voice_client.disconnect()

@bot.tree.command(name='volume', description='Get or set the volume of the music player.', guild=discord.Object(id=GUILD_ID))
@app_commands.describe(volume='Volume level (0-100)')
async def volume(interaction: discord.Interaction, volume: Optional[int] = None):
    """Show the current volume or set it if a value is provided, with smooth transition."""
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        return await interaction.response.send_message("Not connected to a voice channel.")

    guild_id = str(interaction.guild.id)

    if volume is None:
        # Show current volume
        current = int(GUILD_VOLUMES.get(guild_id, 0.5) * 100)
        return await interaction.response.send_message(f"Current volume is {current}%.")

    # Set new volume with smooth transition
    if not (0 <= volume <= 100):
        return await interaction.response.send_message("Volume must be between 0 and 100.")

    old_volume = GUILD_VOLUMES.get(guild_id, 0.5)
    new_volume = volume / 100.0
    GUILD_VOLUMES[guild_id] = new_volume  # Save target volume for this guild
    save_volumes()

    if voice_client.source:
        steps = 20
        step_size = (new_volume - old_volume) / steps
        delay = 0.03  # seconds between steps (adjust for speed)
        current = old_volume
        for _ in range(steps):
            current += step_size
            # Clamp between 0.0 and 1.0
            current = max(0.0, min(1.0, current))
            voice_client.source.volume = current
            await asyncio.sleep(delay)
        # Ensure final value is set
        voice_client.source.volume = new_volume

    await interaction.response.send_message(f"Volume set to {volume}% (smooth transition).")

@bot.tree.command(name='shuffle', description='Shuffle the current song queue.')
async def shuffle(interaction: discord.Interaction):
    """Shuffle the current queue (except the currently playing song)."""
    guild_id = str(interaction.guild.id)
    if guild_id not in SONG_QUEUES or not SONG_QUEUES[guild_id]:
        await interaction.response.send_message("The queue is empty, nothing to shuffle.")
        return

    # Get the currently playing song, if any
    voice_client = interaction.guild.voice_client
    now_playing = None
    if voice_client and voice_client.is_playing() and voice_client.source:
        now_playing = getattr(voice_client.source, "title", None)

    # Shuffle the queue, but keep the first song in place
    queue_list = list(SONG_QUEUES[guild_id])
    if now_playing:
        # Find the currently playing song in the queue
        for i, (url, title) in enumerate(queue_list):
            if title == now_playing:
                # Move it to the front
                queue_list.pop(i)
                queue_list.insert(0, (url, title))
                break

    random.shuffle(queue_list[1:])  # Shuffle the rest of the queue
    SONG_QUEUES[guild_id] = deque(queue_list)

    await interaction.response.send_message("Shuffled the queue.")

# =========================
# === PLAYLIST COMMANDS ===
# =========================

async def playlist_name_autocomplete(interaction: discord.Interaction, current: str):
    user_id = str(interaction.user.id)
    playlists = PLAYLISTS.get(user_id, {})
    return [
        app_commands.Choice(name=pl, value=pl)
        for pl in playlists
        if current.lower() in pl.lower()
    ][:25]

@bot.tree.command(name="playlist", description="Manage your playlists (create, delete, add, remove, view).", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    action="Main action: create, delete, or select",
    name="Playlist name (for create, delete, select)",
    select_action="If selecting: add, remove, or view",
    song="Song name or position (for add/remove)"
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Create", value="create"),
        app_commands.Choice(name="Delete", value="delete"),
        app_commands.Choice(name="Select", value="select"),
    ],
    select_action=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Remove", value="remove"),
        app_commands.Choice(name="View", value="view"),
    ]
)
@app_commands.autocomplete(name=playlist_name_autocomplete)
async def playlist(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    name: Optional[str] = None,
    select_action: Optional[app_commands.Choice[str]] = None,
    song: Optional[str] = None
):
    """Manage your playlists: create, delete, add, remove, view."""
    user_id = str(interaction.user.id)
    action_value = action.value if isinstance(action, app_commands.Choice) else action
    select_action_value = select_action.value if isinstance(select_action, app_commands.Choice) else select_action

    # CREATE
    if action_value == "create":
        if not name:
            return await interaction.response.send_message("You must provide a playlist name.", ephemeral=True)
        if select_action or song:
            return await interaction.response.send_message("Only provide a name when creating.", ephemeral=True)
        if not is_playlist_user(interaction.user.id):
            return await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)
        if user_id not in PLAYLISTS:
            PLAYLISTS[user_id] = {}
        if name in PLAYLISTS[user_id]:
            return await interaction.response.send_message("Playlist already exists.", ephemeral=True)
        PLAYLISTS[user_id][name] = []
        save_playlists(PLAYLISTS)
        return await interaction.response.send_message(f"Playlist '{name}' created.", ephemeral=True)

    # DELETE
    if action_value == "delete":
        if not name:
            return await interaction.response.send_message("You must select a playlist to delete.", ephemeral=True)
        if select_action or song:
            return await interaction.response.send_message("Only provide a playlist name when deleting.", ephemeral=True)
        if not is_playlist_user(interaction.user.id):
            return await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)
        if user_id not in PLAYLISTS or name not in PLAYLISTS[user_id]:
            return await interaction.response.send_message("Playlist not found.", ephemeral=True)
        del PLAYLISTS[user_id][name]
        save_playlists(PLAYLISTS)
        return await interaction.response.send_message(f"Playlist '{name}' deleted.", ephemeral=True)

    # SELECT
    if action_value == "select":
        if not name:
            return await interaction.response.send_message("You must select a playlist.", ephemeral=True)
        if not select_action_value:
            return await interaction.response.send_message("You must choose an action for the selected playlist (add, remove, view).", ephemeral=True)
        if not is_playlist_user(interaction.user.id):
            return await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)
        if user_id not in PLAYLISTS or name not in PLAYLISTS[user_id]:
            return await interaction.response.send_message("Playlist not found.", ephemeral=True)

        # ADD
        if select_action_value == "add":
            if not song:
                return await interaction.response.send_message("You must provide a song name to add.", ephemeral=True)
            await interaction.response.defer(thinking=True, ephemeral=True)
            # Check if song is a playlist URL
            if is_youtube_playlist_url(song):
                entries = await get_playlist_entries(song)
                if not entries:
                    return await interaction.followup.send("No valid songs found in the playlist link.", ephemeral=True)
                for entry in entries:
                    PLAYLISTS[user_id][name].append({"query": entry["url"], "title": entry["title"]})
                save_playlists(PLAYLISTS)
                return await interaction.followup.send(f"Added {len(entries)} songs from playlist link to playlist '{name}'.", ephemeral=True)
            # Otherwise, treat as a single song search
            title = await get_song_metadata(song)
            if not title:
                return await interaction.followup.send("Song not found.", ephemeral=True)
            PLAYLISTS[user_id][name].append({"query": song, "title": title})
            save_playlists(PLAYLISTS)
            return await interaction.followup.send(f"Added '{title}' to playlist '{name}'.", ephemeral=True)

        # REMOVE
        if select_action_value == "remove":
            if not song:
                return await interaction.response.send_message("You must provide a song name or position to remove.", ephemeral=True)
            playlist = PLAYLISTS[user_id][name]
            # Try by position
            if song.isdigit():
                idx = int(song) - 1
                if 0 <= idx < len(playlist):
                    removed = playlist.pop(idx)
                    save_playlists(PLAYLISTS)
                    return await interaction.response.send_message(f"Removed '{removed['title']}' from playlist '{name}'.", ephemeral=True)
                else:
                    return await interaction.response.send_message("Invalid position.", ephemeral=True)
            # Try by name (fuzzy match)
            titles = [s['title'] for s in playlist]
            matches = get_close_matches(song, titles, n=1, cutoff=0.6)
            if matches:
                idx = titles.index(matches[0])
                removed = playlist.pop(idx)
                save_playlists(PLAYLISTS)
                return await interaction.response.send_message(f"Removed '{removed['title']}' from playlist '{name}'.", ephemeral=True)
            else:
                return await interaction.response.send_message("Song not found in playlist.", ephemeral=True)

        # VIEW
        if select_action_value == "view":
            if song:
                return await interaction.response.send_message("No song argument needed for view.", ephemeral=True)
            songs = PLAYLISTS[user_id][name]
            if not songs:
                embed = Embed(title=f"Playlist '{name}'", description="Playlist is empty.")
                return await interaction.response.send_message(embed=embed, ephemeral=True)
            lines = [f"{i+1}. {song['title']}" for i, song in enumerate(songs)]
            return await send_paginated_embed(interaction, lines, f"Playlist '{name}'", ephemeral=True)

    await interaction.response.send_message("Invalid usage or argument combination.", ephemeral=True)

# =========================
# === ADMIN COMMANDS ======
# =========================
@bot.command(name="sync")
async def sync_cmd(ctx):
    """Sync all slash commands to the test guild (admin only, !sync)."""
    if str(ctx.author.id) != "569616893095313408":
        await ctx.send("You are not authorized to use this command.")
        return
    await bot.wait_until_ready()
    test_guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=test_guild)
    cmds = await bot.tree.fetch_commands(guild=test_guild)
    print(f"Fetched commands from Discord: {[cmd.name for cmd in cmds]}")

# =========================
# === HELP COMMAND ========
# =========================

@bot.tree.command(name="help", description="Show help for all commands.")
async def help_command(interaction: discord.Interaction):
    """Show help for all commands, organized by category, with pagination."""
    # Define command categories and their commands
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
                ("/volume [0-100]", "Show or set the volume (smooth transition)."),
                ("/shuffle", "Shuffle the current song queue."),
                ("/playlist", "Manage your playlists (create, delete, add, remove, view)."),
            ]
        },
        {
            "name": "Admin",
            "commands": [
                ("!sync", "Sync all slash commands to the test guild (admin only)."),
            ]
        }
    ]

    # Build pages (one category per page)
    pages = []
    for cat in categories:
        desc = ""
        desc += f"**{cat['name']} Commands**\n\n"
        for cmd, cmd_desc in cat["commands"]:
            desc += f"**{cmd}**\n{cmd_desc}\n\n"
        pages.append([desc.strip()])

    # Use paginated embed helper
    await send_paginated_embed(
        interaction,
        pages,
        title="Bot Help",
        per_page=1,
        ephemeral=True
    )

# --- Add descriptions to commands if missing ---
@bot.tree.command(name='join', description='Join your voice channel.')
async def join(interaction: discord.Interaction):
    """Join the user's voice channel, unless already playing elsewhere."""
    # Check if bot is already playing music in any voice channel in the guild
    for vc in interaction.guild.voice_channels:
        if vc.guild.voice_client and (vc.guild.voice_client.is_playing() or vc.guild.voice_client.is_paused()):
            embed = Embed(title="Already Playing", description="The bot is already playing music in another channel.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
    if not interaction.user.voice or not interaction.user.voice.channel:
        embed = Embed(title="Not Connected", description="You must be in a voice channel to use this command.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    channel = interaction.user.voice.channel
    await channel.connect()
    embed = Embed(title="Joined", description=f"Joined {channel.mention}.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='play', description='Play a song, YouTube playlist, or your saved playlist.')
@app_commands.describe(song_or_playlist='Song query, YouTube playlist link, or playlist name')
async def play(interaction: discord.Interaction, song_or_playlist: str):
    """Play a song by search, YouTube playlist link, or a saved playlist."""
    # Check if bot is already playing music in any voice channel in the guild
    for vc in interaction.guild.voice_channels:
        if vc.guild.voice_client and (vc.guild.voice_client.is_playing() or vc.guild.voice_client.is_paused()):
            embed = Embed(title="Already Playing", description="The bot is already playing music in another channel.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
    await interaction.response.defer(ephemeral=True)
    voice_channel = interaction.user.voice.channel
    if voice_channel is None:
        embed = Embed(title="Not Connected", description="You need to be in a voice channel to play music.")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_channel != voice_client.channel:
        await voice_client.move_to(voice_channel)
    guild_id = str(interaction.guild.id)
    user_id = str(interaction.user.id)
    # Check if it's a playlist name for this user
    playlist = PLAYLISTS.get(user_id, {}).get(song_or_playlist)
    if playlist:
        if SONG_QUEUES.get(guild_id) is None:
            SONG_QUEUES[guild_id] = deque()
        if not playlist:
            await interaction.followup.send("Playlist is empty.")
            return

        # Play the first song immediately
        first_entry = playlist[0]
        ydl_options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "youtube_include_dash_manifest": False,
            "youtube_include_hls_manifest": False,
        }
        results = await search_ytdlp_async("ytsearch1:" + first_entry['query'], ydl_options)
        tracks = results.get('entries', [])
        if not tracks:
            await interaction.followup.send(f"Could not find: {first_entry['title']}")
            return
        first_track = tracks[0]
        audio_url = first_track["url"]
        title = first_track.get("title", first_entry['title'])
        SONG_QUEUES[guild_id].append((audio_url, title))

        # Start playing if not already
        if not voice_client.is_playing() and not voice_client.is_paused():
            await play_next_song(voice_client, guild_id, interaction.channel)

        # Download the rest in the background
        async def queue_rest(entries):
            for entry in entries:
                ydl_options = {
                    "format": "bestaudio/best",
                    "noplaylist": True,
                    "youtube_include_dash_manifest": False,
                    "youtube_include_hls_manifest": False,
                }
                results = await search_ytdlp_async("ytsearch1:" + entry['query'], ydl_options)
                tracks = results.get('entries', [])
                if not tracks:
                    continue
                first_track = tracks[0]
                audio_url = first_track["url"]
                title = first_track.get("title", entry['title'])
                SONG_QUEUES[guild_id].append((audio_url, title))

        # Schedule background task for the rest of the playlist
        asyncio.create_task(queue_rest(playlist[1:]))

        await interaction.followup.send(f"Queued playlist '{song_or_playlist}'.")
        return
    # Otherwise, treat as a song or playlist link
    if is_youtube_playlist_url(song_or_playlist):
        entries = await get_playlist_entries(song_or_playlist)
        if not entries:
            await interaction.followup.send("No valid songs found in the playlist link.")
            return
        if SONG_QUEUES.get(guild_id) is None:
            SONG_QUEUES[guild_id] = deque()

        # Play the first song immediately
        first_entry = entries[0]
        ydl_options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "youtube_include_dash_manifest": False,
            "youtube_include_hls_manifest": False,
        }
        results = await search_ytdlp_async(first_entry['url'], ydl_options)
        tracks = results.get('entries', [])
        if not tracks:
            await interaction.followup.send(f"Could not find: {first_entry['title']}")
            return
        first_track = tracks[0]
        audio_url = first_track["url"]
        title = first_track.get("title", first_entry['title'])
        SONG_QUEUES[guild_id].append((audio_url, title))

        # Start playing if not already
        if not voice_client.is_playing() and not voice_client.is_paused():
            await play_next_song(voice_client, guild_id, interaction.channel)

        # Download the rest in the background
        async def queue_rest(entries):
            for entry in entries:
                ydl_options = {
                    "format": "bestaudio/best",
                    "noplaylist": True,
                    "youtube_include_dash_manifest": False,
                    "youtube_include_hls_manifest": False,
                }
                results = await search_ytdlp_async(entry['url'], ydl_options)
                tracks = results.get('entries', [])
                if not tracks:
                    continue
                first_track = tracks[0]
                audio_url = first_track["url"]
                title = first_track.get("title", entry['title'])
                SONG_QUEUES[guild_id].append((audio_url, title))

        # Schedule background task for the rest of the playlist
        asyncio.create_task(queue_rest(entries[1:]))

        await interaction.followup.send(f"Queued playlist from link.")
        return

    # Otherwise, treat as a song query (existing code)
    ydl_options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
    }
    query = "ytsearch1:" + song_or_playlist
    results = await search_ytdlp_async(query, ydl_options)
    tracks = results.get('entries', [])
    if not tracks:
        await interaction.followup.send("No results found.")
        return
    first_track = tracks[0]
    audio_url = first_track["url"]
    title = first_track.get("title", "Unknown Title")
    if SONG_QUEUES.get(guild_id) is None:
        SONG_QUEUES[guild_id] = deque()
    SONG_QUEUES[guild_id].append((audio_url, title))
    if voice_client.is_playing() or voice_client.is_paused():
        await interaction.followup.send(f"Added to queue: {title}")
    else:
        await interaction.followup.send(f"Now playing: {title}")
        await play_next_song(voice_client, guild_id, interaction.channel)

@bot.tree.command(name='queue', description='Show the current song queue with pagination.')
async def queue(interaction: discord.Interaction):
    """Show the current queue with pagination and now playing."""
    guild_id = str(interaction.guild.id)
    if guild_id not in SONG_QUEUES or not SONG_QUEUES[guild_id]:
        embed = Embed(title="Current Queue", description="The queue is currently empty.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Show now playing if possible
    voice_client = interaction.guild.voice_client
    now_playing = None
    if voice_client and voice_client.is_playing() and voice_client.source:
        # Try to get the title from the current source if possible
        # If you store the current song title elsewhere, use that
        now_playing = getattr(voice_client.source, "title", None)
    if not now_playing and SONG_QUEUES[guild_id]:
        # Fallback: show the first in queue as now playing
        now_playing = SONG_QUEUES[guild_id][0][1]

    lines = []
    if now_playing:
        lines.append(f"**Now Playing:** {now_playing}\n")
    # List the rest of the queue (skip the first if it's now playing)
    queue_lines = [f"{i+1}. {title}" for i, (_, title) in enumerate(SONG_QUEUES[guild_id])]
    lines.extend(queue_lines)
    await send_paginated_embed(interaction, lines, "Current Queue", ephemeral=True)

@bot.tree.command(name='skip', description='Skip the currently playing song.')
async def skip(interaction: discord.Interaction):
    """Skip the currently playing song."""
    if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("Skipped the current song.")
    else:
        await interaction.response.send_message("No song is currently playing.")

@bot.tree.command(name='pause', description='Pause the current song.')
async def pause(interaction: discord.Interaction):
    """Pause the current song."""
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        return await interaction.response.send_message("Not connected to a voice channel.")
    if not voice_client.is_playing():
        return await interaction.response.send_message("No song is currently playing.")
    voice_client.pause()
    await interaction.response.send_message("Paused the current song.")

@bot.tree.command(name='resume', description='Resume the paused song.')
async def resume(interaction: discord.Interaction):
    """Resume the paused song."""
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        return await interaction.response.send_message("Not connected to a voice channel.")
    if not voice_client.is_paused():
        return await interaction.response.send_message("No song is currently paused.")
    voice_client.resume()
    await interaction.response.send_message("Resumed the current song.")

@bot.tree.command(name='stop', description='Stop playback and clear the queue.')
async def stop(interaction: discord.Interaction):
    """Stop playback and clear the queue."""
    await interaction.response.defer()
    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_connected():
        await interaction.followup.send("Not connected to a voice channel.")
        return
    guild_id_str = str(interaction.guild.id)
    if guild_id_str in SONG_QUEUES:
        SONG_QUEUES[guild_id_str].clear()
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()
    await interaction.followup.send("Stopped the current song and cleared the queue.")
    await voice_client.disconnect()

@bot.tree.command(name='volume', description='Get or set the volume of the music player.')
@app_commands.describe(volume='Volume level (0-100)')
async def volume(interaction: discord.Interaction, volume: Optional[int] = None):
    """Show the current volume or set it if a value is provided, with smooth transition."""
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        return await interaction.response.send_message("Not connected to a voice channel.")

    guild_id = str(interaction.guild.id)

    if volume is None:
        # Show current volume
        current = int(GUILD_VOLUMES.get(guild_id, 0.5) * 100)
        return await interaction.response.send_message(f"Current volume is {current}%.")

    # Set new volume with smooth transition
    if not (0 <= volume <= 100):
        return await interaction.response.send_message("Volume must be between 0 and 100.")

    old_volume = GUILD_VOLUMES.get(guild_id, 0.5)
    new_volume = volume / 100.0
    GUILD_VOLUMES[guild_id] = new_volume  # Save target volume for this guild
    save_volumes()

    if voice_client.source:
        steps = 20
        step_size = (new_volume - old_volume) / steps
        delay = 0.03  # seconds between steps (adjust for speed)
        current = old_volume
        for _ in range(steps):
            current += step_size
            # Clamp between 0.0 and 1.0
            current = max(0.0, min(1.0, current))
            voice_client.source.volume = current
            await asyncio.sleep(delay)
        # Ensure final value is set
        voice_client.source.volume = new_volume

    await interaction.response.send_message(f"Volume set to {volume}% (smooth transition).")

@bot.tree.command(name='shuffle', description='Shuffle the current song queue.')
async def shuffle(interaction: discord.Interaction):
    """Shuffle the current queue (except the currently playing song)."""
    guild_id = str(interaction.guild.id)
    if guild_id not in SONG_QUEUES or not SONG_QUEUES[guild_id]:
        await interaction.response.send_message("The queue is empty, nothing to shuffle.")
        return

    # Get the currently playing song, if any
    voice_client = interaction.guild.voice_client
    now_playing = None
    if voice_client and voice_client.is_playing() and voice_client.source:
        now_playing = getattr(voice_client.source, "title", None)

    # Shuffle the queue, but keep the first song in place
    queue_list = list(SONG_QUEUES[guild_id])
    if now_playing:
        # Find the currently playing song in the queue
        for i, (url, title) in enumerate(queue_list):
            if title == now_playing:
                # Move it to the front
                queue_list.pop(i)
                queue_list.insert(0, (url, title))
                break

    random.shuffle(queue_list[1:])  # Shuffle the rest of the queue
    SONG_QUEUES[guild_id] = deque(queue_list)

    await interaction.response.send_message("Shuffled the queue.")

# =========================
# === PLAYBACK LOGIC ======
# =========================

async def play_next_song(voice_client, guild_id, channel):
    """Play the next song in the queue, or disconnect if empty after 1 minute. Updates channel topic with now playing."""
    if SONG_QUEUES[guild_id]:
        audio_url, title = SONG_QUEUES[guild_id].popleft()
        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn"
        }
        # Use the saved volume, or default to 0.5
        volume = GUILD_VOLUMES.get(guild_id, 0.5)
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(audio_url, **ffmpeg_options, executable="bin\\ffmpeg\\ffmpeg.exe"),
            volume=volume
        )
        def after_play(error):
            if error:
                print(f"Error playing {title}: {error}")
            asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, channel), bot.loop)
        voice_client.play(source, after=after_play)
        # Update channel topic with now playing
        try:
            await channel.edit(topic=f"Now playing: {title}")
        except Exception as e:
            print(f"Failed to update channel topic: {e}")
    else:
        # Wait 60 seconds before disconnecting, in case new songs are added
        await asyncio.sleep(60)
        # If queue is still empty and still connected, disconnect
        if not SONG_QUEUES[guild_id] and voice_client.is_connected():
            await voice_client.disconnect()
            SONG_QUEUES[guild_id] = deque()
            # Clear channel topic
            try:
                await channel.edit(topic="")
            except Exception as e:
                print(f"Failed to clear channel topic: {e}")

# =========================
# === BOT RUN ============
# =========================

if __name__ == "__main__":
    bot.run(TOKEN)