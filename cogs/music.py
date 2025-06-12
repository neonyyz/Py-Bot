import discord
from discord.ext import commands, tasks
from discord import app_commands, Embed
from typing import Optional
from collections import deque
import random
import asyncio
import os
import json

from utils.persistence import SONG_QUEUES, GUILD_VOLUMES, PLAYLISTS, save_volumes, save_playlists
from utils.ytdlp import search_ytdlp_async, get_song_metadata, is_youtube_playlist_url, get_playlist_entries
from utils.pagination import send_paginated_embed

GUILD_ID = int(os.getenv("GUILD_ID", "876523812559159336"))
ALLOWED_USERS = set(filter(None, os.getenv("ALLOWED_USERS", "").split(",")))

def is_playlist_user(user_id):
    return str(user_id) in ALLOWED_USERS

async def playlist_name_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=pl, value=pl)
        for pl in PLAYLISTS.get(str(interaction.user.id), {})
        if current.lower() in pl.lower()
    ][:25]

class PlayMenuView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=60)
        self.cog = cog

    @discord.ui.button(label="Play from Search", style=discord.ButtonStyle.green)
    async def play_search(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PlaySearchModal(self.cog))

    @discord.ui.button(label="Play from URL", style=discord.ButtonStyle.blurple)
    async def play_url(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PlayURLModal(self.cog))

    @discord.ui.button(label="Play from Playlist", style=discord.ButtonStyle.gray)
    async def play_playlist(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        playlists = list(PLAYLISTS.get(user_id, {}).keys())
        if not playlists:
            await interaction.response.send_message("You have no playlists.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Select a playlist to play:",
            view=PlaylistDropdownView(self.cog, playlists),
            ephemeral=True
        )

class PlaySearchModal(discord.ui.Modal, title="Play from Search"):
    query = discord.ui.TextInput(label="Song name or keywords", required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.play.callback(self.cog, interaction, self.query.value)

class PlayURLModal(discord.ui.Modal, title="Play from URL"):
    url = discord.ui.TextInput(label="YouTube URL or playlist", required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.play.callback(self.cog, interaction, self.url.value)

class PlaylistDropdown(discord.ui.Select):
    def __init__(self, cog, playlists):
        options = [discord.SelectOption(label=pl, value=pl) for pl in playlists]
        super().__init__(placeholder="Select a playlist...", min_values=1, max_values=1, options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await self.cog.play.callback(self.cog, interaction, self.values[0])

class PlaylistDropdownView(discord.ui.View):
    def __init__(self, cog, playlists):
        super().__init__(timeout=60)
        self.add_item(PlaylistDropdown(cog, playlists))

class MusicControlView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Play", style=discord.ButtonStyle.gray, custom_id="music_play", row=0)
    async def play(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "How would you like to play music?",
            view=PlayMenuView(self.cog),
            ephemeral=True
        )

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.gray, custom_id="music_pause", row=0)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.pause.callback(self.cog, interaction)

    @discord.ui.button(label="Resume", style=discord.ButtonStyle.gray, custom_id="music_resume", row=0)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.resume.callback(self.cog, interaction)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.gray, custom_id="music_skip", row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.skip.callback(self.cog, interaction)

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.gray, custom_id="music_queue", row=1)
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.queue.callback(self.cog, interaction)

    @discord.ui.button(label="Playlist", style=discord.ButtonStyle.gray, custom_id="music_playlist", row=1)
    async def playlist(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Playlist management:",
            view=PlaylistMenuView(self.cog),
            ephemeral=True
        )

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.gray, custom_id="music_loop", row=1)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild.id)
        current = self.cog.loop_mode.get(guild_id, "off")
        if current == "off":
            new_mode = "song"
            msg = "Loop mode set to **song** (repeat current song)."
        elif current == "song":
            new_mode = "queue"
            msg = "Loop mode set to **queue** (repeat queue)."
        else:
            new_mode = "off"
            msg = "Loop mode turned **off**."
        self.cog.loop_mode[guild_id] = new_mode
        embed = Embed(title="Loop Mode", description=msg)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.gray, custom_id="music_shuffle", row=1)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.shuffle.callback(self.cog, interaction)

    @discord.ui.button(label="Volume", style=discord.ButtonStyle.gray, custom_id="music_volume", row=2)
    async def volume(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = interaction.guild.voice_client
        guild_id = str(interaction.guild.id)
        current = int(GUILD_VOLUMES.get(guild_id, 0.5) * 100)
        await interaction.response.send_modal(VolumeModal(self.cog))

    @discord.ui.button(label="Now Playing", style=discord.ButtonStyle.gray, custom_id="music_nowplaying", row=2)
    async def nowplaying(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild.id)
        voice_client = interaction.guild.voice_client
        now_playing = None
        if voice_client and voice_client.is_playing() and voice_client.source:
            now_playing = getattr(voice_client.source, "title", None)
        elif guild_id in self.cog.last_played:
            now_playing = self.cog.last_played[guild_id][1]
        if now_playing:
            embed = Embed(title="Now Playing", description=now_playing)
        else:
            embed = Embed(title="Now Playing", description="Nothing is currently playing.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_topic_update = 0
        self.loop_mode = {}
        self.last_played = {}
        self.queue_task = {}
        self.music_channel_id, self.interface_message_id = load_interface_state()
        self.interface_task = None

    async def cog_load(self):
        self.bot.add_view(MusicControlView(self))
        # Try to restore the persistent interface on startup
        await self.send_or_update_interface()
        await self.start_interface_task()

    async def send_or_update_interface(self):
        if not self.music_channel_id:
            return
        channel = self.bot.get_channel(self.music_channel_id)
        if not channel:
            return
        embed = discord.Embed(
            title="Ayanokoji Interface",
            description=(
                "This interface can be used to manage or control the music player\n"
                "- here i'll add a picture once i edit it, write picture here -\n"
                "Press the buttons below to use the interface"
            ),
            color=discord.Color.blurple()
        )
        view = MusicControlView(self)
        message = None
        if self.interface_message_id:
            try:
                message = await channel.fetch_message(self.interface_message_id)
                await message.edit(embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                try:
                    old_message = await channel.fetch_message(self.interface_message_id)
                    await old_message.delete()
                except Exception:
                    pass  # Message truly doesn't exist or can't be deleted
                message = await channel.send(embed=embed, view=view)
                self.interface_message_id = message.id
                save_interface_state(self.music_channel_id, self.interface_message_id)
        else:
            message = await channel.send(embed=embed, view=view)
            self.interface_message_id = message.id
            save_interface_state(self.music_channel_id, self.interface_message_id)

    async def start_interface_task(self):
        if self.interface_task:
            self.interface_task.cancel()
        async def updater():
            while True:
                await self.send_or_update_interface()
                await asyncio.sleep(30)
        self.interface_task = asyncio.create_task(updater())
    
    @app_commands.command(name="select_music_channel", description="Select the channel for the persistent music interface.")
    @app_commands.describe(channel="The channel to use for the music interface")
    async def select_music_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        self.music_channel_id = channel.id
        self.interface_message_id = None  # Reset so a new message is created
        await self.send_or_update_interface()
        await self.start_interface_task()
        await interaction.response.send_message(f"Music interface will be shown in {channel.mention}.", ephemeral=True)

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name='join', description='Join your voice channel.')
    async def join(self, interaction: discord.Interaction):
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

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name='play', description='Play a song, YouTube playlist, or your saved playlist.')
    @app_commands.describe(song_or_playlist='Song query, YouTube playlist link, or playlist name')
    async def play(self, interaction: discord.Interaction, song_or_playlist: str):
        voice_client = interaction.guild.voice_client
        user_voice = interaction.user.voice.channel if interaction.user.voice else None

        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            if user_voice is None or voice_client.channel != user_voice:
                embed = Embed(
                    title="Already Playing",
                    description=f"The bot is already playing music in {voice_client.channel.mention}."
                )
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
        playlist = PLAYLISTS.get(user_id, {}).get(song_or_playlist)

        # Play from saved playlist
        if playlist:
            if SONG_QUEUES.get(guild_id) is None:
                SONG_QUEUES[guild_id] = deque()
            if not playlist:
                embed = Embed(title="Playlist Empty", description="Playlist is empty.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Extract and queue the first song
            first_entry = playlist[0]
            query = first_entry['query']
            if is_youtube_playlist_url(query):
                entries_from_link = await get_playlist_entries(query)
                if not entries_from_link:
                    embed = Embed(title="Not Found", description=f"Could not find: {first_entry['title']}")
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return
                first_subentry = entries_from_link[0]
                ydl_options = {
                    "format": "bestaudio/best",
                    "youtube_include_dash_manifest": False,
                    "youtube_include_hls_manifest": False,
                }
                results = await search_ytdlp_async(first_subentry['url'], ydl_options)
                tracks = results.get('entries', [])
                if not tracks and results.get("url"):
                    tracks = [results]
                if tracks:
                    first_track = tracks[0]
                    audio_url = first_track["url"]
                    title = first_track.get("title", first_subentry['title'])
                    SONG_QUEUES[guild_id].append((audio_url, title))
                    if not voice_client.is_playing() and not voice_client.is_paused():
                        await self.play_next_song(voice_client, guild_id, interaction.channel)
                # Queue the rest of the playlist link in the background
                async def queue_rest(entries):
                    for subentry in entries:
                        results = await search_ytdlp_async(subentry['url'], ydl_options)
                        tracks = results.get('entries', [])
                        if not tracks and results.get("url"):
                            tracks = [results]
                        if not tracks:
                            continue
                        first_track = tracks[0]
                        audio_url = first_track["url"]
                        title = first_track.get("title", subentry['title'])
                        SONG_QUEUES[guild_id].append((audio_url, title))
                # Only pass the rest of the playlist (excluding the first song)
                task = asyncio.create_task(queue_rest(entries_from_link[1:]))
                self.queue_task[guild_id] = task
            else:
                ydl_options = {
                    "format": "bestaudio/best",
                    "noplaylist": True,
                    "youtube_include_dash_manifest": False,
                    "youtube_include_hls_manifest": False,
                }
                if query.startswith("http://") or query.startswith("https://"):
                    search_term = query
                else:
                    search_term = f"ytsearch1:{query}"
                results = await search_ytdlp_async(search_term, ydl_options)
                tracks = results.get('entries', [])
                if not tracks and results.get("url"):
                    tracks = [results]
                if not tracks:
                    embed = Embed(title="Not Found", description=f"Could not find: {first_entry.get('title', query)}")
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return
                first_track = tracks[0]
                audio_url = first_track["url"]
                title = first_track.get("title", first_entry.get('title', query))
                SONG_QUEUES[guild_id].append((audio_url, title))
                if not voice_client.is_playing() and not voice_client.is_paused():
                    await self.play_next_song(voice_client, guild_id, interaction.channel)

                # Queue the rest in the background
                async def queue_rest(entries):
                    for entry in entries:
                        q = entry['query']
                        if is_youtube_playlist_url(q):
                            subentries = await get_playlist_entries(q)
                            for subentry in subentries:
                                ydl_options2 = {
                                    "format": "bestaudio/best",
                                    "youtube_include_dash_manifest": False,
                                    "youtube_include_hls_manifest": False,
                                }
                                results2 = await search_ytdlp_async(subentry['url'], ydl_options2)
                                tracks2 = results2.get('entries', [])
                                if not tracks2 and results2.get("url"):
                                    tracks2 = [results2]
                                if not tracks2:
                                    continue
                                first_track2 = tracks2[0]
                                audio_url2 = first_track2["url"]
                                title2 = first_track2.get("title", subentry['title'])
                                SONG_QUEUES[guild_id].append((audio_url2, title2))
                        else:
                            ydl_options2 = {
                                "format": "bestaudio/best",
                                "noplaylist": True,
                                "youtube_include_dash_manifest": False,
                                "youtube_include_hls_manifest": False,
                            }
                            if q.startswith("http://") or q.startswith("https://"):
                                search_term2 = q
                            else:
                                search_term2 = "ytsearch1:" + q
                            results2 = await search_ytdlp_async(search_term2, ydl_options2)
                            tracks2 = results2.get('entries', [])
                            if not tracks2 and results2.get("url"):
                                tracks2 = [results2]
                            if not tracks2:
                                continue
                            first_track2 = tracks2[0]
                            audio_url2 = first_track2["url"]
                            title2 = first_track2.get("title", entry.get('title', q))
                            SONG_QUEUES[guild_id].append((audio_url2, title2))
                task = asyncio.create_task(queue_rest(playlist[1:]))
                self.queue_task[guild_id] = task

            embed = Embed(title="Queued Playlist", description=f"Queued playlist '{song_or_playlist}'.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Play from YouTube playlist link
        if is_youtube_playlist_url(song_or_playlist):
            entries = await get_playlist_entries(song_or_playlist)
            if not entries:
                embed = Embed(title="Not Found", description="No valid songs found in the playlist link.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            if SONG_QUEUES.get(guild_id) is None:
                SONG_QUEUES[guild_id] = deque()
            ydl_options = {
                "format": "bestaudio/best",
                "youtube_include_dash_manifest": False,
                "youtube_include_hls_manifest": False,
            }
            first_entry = entries[0]
            results = await search_ytdlp_async(first_entry['url'], ydl_options)
            tracks = results.get('entries', [])
            if not tracks and results.get("url"):
                tracks = [results]
            if tracks:
                first_track = tracks[0]
                audio_url = first_track["url"]
                title = first_track.get("title", first_entry['title'])
                SONG_QUEUES[guild_id].append((audio_url, title))
                if not voice_client.is_playing() and not voice_client.is_paused():
                    await self.play_next_song(voice_client, guild_id, interaction.channel)
            else:
                embed = Embed(title="Not Found", description="Could not extract the first song from the playlist.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Queue the rest in the background
            async def queue_rest(entries):
                for entry in entries:
                    results = await search_ytdlp_async(entry['url'], ydl_options)
                    tracks = results.get('entries', [])
                    if not tracks and results.get("url"):
                        tracks = [results]
                    if not tracks:
                        continue
                    first_track = tracks[0]
                    audio_url = first_track["url"]
                    title = first_track.get("title", entry['title'])
                    SONG_QUEUES[guild_id].append((audio_url, title))
            task = asyncio.create_task(queue_rest(entries[1:]))
            self.queue_task[guild_id] = task

            embed = Embed(title="Queued Playlist", description="Queued playlist from link.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Otherwise, treat as a single song or search query
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
            embed = Embed(title="Not Found", description="No results found.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        first_track = tracks[0]
        audio_url = first_track["url"]
        title = first_track.get("title", "Unknown Title")
        if SONG_QUEUES.get(guild_id) is None:
            SONG_QUEUES[guild_id] = deque()
        SONG_QUEUES[guild_id].append((audio_url, title))
        if voice_client.is_playing() or voice_client.is_paused():
            embed = Embed(title="Added to Queue", description=title)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            embed = Embed(title="Now Playing", description=title)
            await interaction.followup.send(embed=embed, ephemeral=True)
            await self.play_next_song(voice_client, guild_id, interaction.channel)

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name='queue', description='Show the current song queue with pagination.')
    async def queue(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        voice_client = interaction.guild.voice_client
        now_playing = None

        # Get the currently playing song if possible
        if voice_client and voice_client.is_playing() and voice_client.source:
            now_playing = getattr(voice_client.source, "title", None)
        elif guild_id in self.last_played:
            now_playing = self.last_played[guild_id][1]

        queue_list = list(SONG_QUEUES.get(guild_id, []))

        # If queue is empty but a song is playing, show now playing only
        if not queue_list and now_playing:
            embed = Embed(title="Now Playing", description=now_playing)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not queue_list:
            embed = Embed(title="Current Queue", description="The queue is currently empty.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        lines = []
        if now_playing:
            lines.append(f"**Now Playing:** {now_playing}\n")
        # Remove the now playing song from the queue display if it's at the front
        if queue_list and now_playing and queue_list[0][1] == now_playing:
            queue_list = queue_list[1:]
        queue_lines = [f"{i+1}. {title}" for i, (_, title) in enumerate(queue_list)]
        lines.extend(queue_lines)
        await send_paginated_embed(interaction, lines, "Current Queue", ephemeral=True)

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name='skip', description='Skip the currently playing song.')
    async def skip(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
            embed = Embed(title="Skipped", description="Skipped the current song.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            embed = Embed(title="Not Playing", description="No song is currently playing.")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name='pause', description='Pause the current song.')
    async def pause(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            embed = Embed(title="Not Connected", description="Not connected to a voice channel.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if not voice_client.is_playing():
            embed = Embed(title="Not Playing", description="No song is currently playing.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        voice_client.pause()
        embed = Embed(title="Paused", description="Paused the current song.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name='resume', description='Resume the paused song.')
    async def resume(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            embed = Embed(title="Not Connected", description="Not connected to a voice channel.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if not voice_client.is_paused():
            embed = Embed(title="Not Paused", description="No song is currently paused.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        voice_client.resume()
        embed = Embed(title="Resumed", description="Resumed the current song.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name='stop', description='Stop playback and clear the queue.')
    async def stop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            embed = Embed(title="Not Connected", description="Not connected to a voice channel.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        guild_id_str = str(interaction.guild.id)
        if guild_id_str in self.queue_task:
            task = self.queue_task[guild_id_str]
            if not task.done():
                task.cancel()
            del self.queue_task[guild_id_str]
        if guild_id_str in SONG_QUEUES:
            SONG_QUEUES[guild_id_str].clear()
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
        await interaction.followup.send(embed=Embed(title="Stopped", description="Stopped the current song and cleared the queue."), ephemeral=True)
        await voice_client.disconnect()

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name='volume', description='Get or set the volume of the music player.')
    @app_commands.describe(volume='Volume level (0-100)')
    async def volume(self, interaction: discord.Interaction, volume: Optional[int] = None):
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            embed = Embed(title="Not Connected", description="Not connected to a voice channel.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        if volume is None:
            current = int(GUILD_VOLUMES.get(guild_id, 0.5) * 100)
            embed = Embed(title="Volume", description=f"Current volume is {current}%.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if not (0 <= volume <= 100):
            embed = Embed(title="Invalid Volume", description="Volume must be between 0 and 100.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        old_volume = GUILD_VOLUMES.get(guild_id, 0.5)
        new_volume = volume / 100.0
        GUILD_VOLUMES[guild_id] = new_volume
        save_volumes()
        if voice_client.source:
            steps = 20
            step_size = (new_volume - old_volume) / steps
            delay = 0.03
            current = old_volume
            for _ in range(steps):
                current += step_size
                current = max(0.0, min(1.0, current))
                voice_client.source.volume = current
                await asyncio.sleep(delay)
            voice_client.source.volume = new_volume
        embed = Embed(title="Volume", description=f"Volume set to {volume}%.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name='shuffle', description='Shuffle the current song queue.')
    async def shuffle(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        if guild_id not in SONG_QUEUES or not SONG_QUEUES[guild_id]:
            embed = Embed(title="Queue Empty", description="The queue is empty, nothing to shuffle.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        voice_client = interaction.guild.voice_client
        now_playing = None
        if voice_client and voice_client.is_playing() and voice_client.source:
            now_playing = getattr(voice_client.source, "title", None)
        queue_list = list(SONG_QUEUES[guild_id])
        if now_playing:
            for i, (url, title) in enumerate(queue_list):
                if title == now_playing:
                    queue_list.pop(i)
                    queue_list.insert(0, (url, title))
                    break
        if len(queue_list) > 1:
            rest = queue_list[1:]
            random.shuffle(rest)
            queue_list[1:] = rest
        SONG_QUEUES[guild_id] = deque(queue_list)
        embed = Embed(title="Shuffled", description="Shuffled the queue.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name="playlist", description="Manage your playlists (create, delete, add, remove, view).")
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
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        name: Optional[str] = None,
        select_action: Optional[app_commands.Choice[str]] = None,
        song: Optional[str] = None
    ):
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
                if is_youtube_playlist_url(song):
                    entries = await get_playlist_entries(song)
                    if not entries:
                        return await interaction.followup.send("No valid songs found in the playlist link.", ephemeral=True)
                    for entry in entries:
                        PLAYLISTS[user_id][name].append({"query": entry["url"], "title": entry["title"]})
                    save_playlists(PLAYLISTS)
                    return await interaction.followup.send(f"Added {len(entries)} songs from playlist link to playlist '{name}'.", ephemeral=True)
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
                if song.isdigit():
                    idx = int(song) - 1
                    if 0 <= idx < len(playlist):
                        removed = playlist.pop(idx)
                        save_playlists(PLAYLISTS)
                        return await interaction.response.send_message(f"Removed '{removed['title']}' from playlist '{name}'.", ephemeral=True)
                    else:
                        return await interaction.response.send_message("Invalid position.", ephemeral=True)
                from difflib import get_close_matches
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

    @app_commands.guilds(GUILD_ID)
    @app_commands.command(name='loop', description='Set loop mode: song (repeat current), queue (repeat queue), or off.')
    @app_commands.describe(mode='Loop mode: song, queue, or off')
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Off", value="off"),
            app_commands.Choice(name="Song", value="song"),
            app_commands.Choice(name="Queue", value="queue"),
        ]
    )
    async def loop(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str]
    ):
        guild_id = str(interaction.guild.id)
        self.loop_mode[guild_id] = mode.value
        embed = Embed(title="Loop Mode", description=f"Loop mode set to **{mode.value}**.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def play_next_song(self, voice_client, guild_id, channel):
        queue_snapshot = list(SONG_QUEUES[guild_id]) if guild_id in SONG_QUEUES else []
        if SONG_QUEUES[guild_id]:
            audio_url, title = SONG_QUEUES[guild_id].popleft()
            self.last_played[guild_id] = (audio_url, title)
            ffmpeg_options = {
                "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                "options": "-vn"
            }
            volume = GUILD_VOLUMES.get(guild_id, 0.5)
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(audio_url, **ffmpeg_options, executable="ffmpeg"),
                volume=volume
            )
            source.title = title  # <-- Add this line!
            def after_play(error):
                if error:
                    print(f"Error playing {title}: {error}")
                asyncio.run_coroutine_threadsafe(self.play_next_song(voice_client, guild_id, channel), self.bot.loop)
            voice_client.play(source, after=after_play)
        else:
            mode = self.loop_mode.get(guild_id, "off")
            if mode == "song" and guild_id in self.last_played:
                audio_url, title = self.last_played[guild_id]
                SONG_QUEUES[guild_id] = deque([(audio_url, title)])
                await self.play_next_song(voice_client, guild_id, channel)
                return
            elif mode == "queue" and queue_snapshot:
                SONG_QUEUES[guild_id] = deque(queue_snapshot)
                await self.play_next_song(voice_client, guild_id, channel)
                return
            await asyncio.sleep(60)
            if not SONG_QUEUES[guild_id] and voice_client.is_connected():
                await voice_client.disconnect()
                SONG_QUEUES[guild_id] = deque()

INTERFACE_STATE_FILE = "music_interface.json"

def save_interface_state(channel_id, message_id):
    with open(INTERFACE_STATE_FILE, "w") as f:
        json.dump({"channel_id": channel_id, "message_id": message_id}, f)

def load_interface_state():
    try:
        with open(INTERFACE_STATE_FILE, "r") as f:
            data = json.load(f)
            return data.get("channel_id"), data.get("message_id")
    except Exception:
        return None, None

async def setup(bot):
    await bot.add_cog(Music(bot))

class PlaylistMenuView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=60)
        self.cog = cog

    @discord.ui.button(label="Create", style=discord.ButtonStyle.green)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PlaylistCreateModal(self.cog))

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.red)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        playlists = list(PLAYLISTS.get(user_id, {}).keys())
        if not playlists:
            await interaction.response.send_message("You have no playlists.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Select a playlist to delete:",
            view=PlaylistDeleteDropdownView(self.cog, playlists),
            ephemeral=True
        )

    @discord.ui.button(label="View", style=discord.ButtonStyle.blurple)
    async def view_(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        playlists = list(PLAYLISTS.get(user_id, {}).keys())
        if not playlists:
            await interaction.response.send_message("You have no playlists.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Select a playlist to view:",
            view=PlaylistViewDropdownView(self.cog, playlists),
            ephemeral=True
        )

    @discord.ui.button(label="Add Song", style=discord.ButtonStyle.gray)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        playlists = list(PLAYLISTS.get(user_id, {}).keys())
        if not playlists:
            await interaction.response.send_message("You have no playlists.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Select a playlist to add a song to:",
            view=PlaylistAddDropdownView(self.cog, playlists),
            ephemeral=True
        )

    @discord.ui.button(label="Remove Song", style=discord.ButtonStyle.gray)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        playlists = list(PLAYLISTS.get(user_id, {}).keys())
        if not playlists:
            await interaction.response.send_message("You have no playlists.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Select a playlist to remove a song from:",
            view=PlaylistRemoveDropdownView(self.cog, playlists),
            ephemeral=True
        )

class PlaylistCreateModal(discord.ui.Modal, title="Create Playlist"):
    name = discord.ui.TextInput(label="Playlist Name", required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        name = self.name.value
        if user_id not in PLAYLISTS:
            PLAYLISTS[user_id] = {}
        if name in PLAYLISTS[user_id]:
            await interaction.response.send_message("Playlist already exists.", ephemeral=True)
            return
        PLAYLISTS[user_id][name] = []
        save_playlists(PLAYLISTS)
        await interaction.response.send_message(f"Playlist '{name}' created.", ephemeral=True)

class PlaylistDeleteDropdown(discord.ui.Select):
    def __init__(self, cog, playlists):
        options = [discord.SelectOption(label=pl, value=pl) for pl in playlists]
        super().__init__(placeholder="Select a playlist...", min_values=1, max_values=1, options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        name = self.values[0]
        if name in PLAYLISTS.get(user_id, {}):
            del PLAYLISTS[user_id][name]
            save_playlists(PLAYLISTS)
            await interaction.response.send_message(f"Playlist '{name}' deleted.", ephemeral=True)
        else:
            await interaction.response.send_message("Playlist not found.", ephemeral=True)

class PlaylistDeleteDropdownView(discord.ui.View):
    def __init__(self, cog, playlists):
        super().__init__(timeout=60)
        self.add_item(PlaylistDeleteDropdown(cog, playlists))

class PlaylistViewDropdown(discord.ui.Select):
    def __init__(self, cog, playlists):
        options = [discord.SelectOption(label=pl, value=pl) for pl in playlists]
        super().__init__(placeholder="Select a playlist...", min_values=1, max_values=1, options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        name = self.values[0]
        songs = PLAYLISTS.get(user_id, {}).get(name, [])
        if not songs:
            embed = Embed(title=f"Playlist '{name}'", description="Playlist is empty.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        lines = [f"{i+1}. {song['title']}" for i, song in enumerate(songs)]
        await send_paginated_embed(interaction, lines, f"Playlist '{name}'", ephemeral=True)

class PlaylistViewDropdownView(discord.ui.View):
    def __init__(self, cog, playlists):
        super().__init__(timeout=60)
        self.add_item(PlaylistViewDropdown(cog, playlists))

class PlaylistAddDropdown(discord.ui.Select):
    def __init__(self, cog, playlists):
        options = [discord.SelectOption(label=pl, value=pl) for pl in playlists]
        super().__init__(placeholder="Select a playlist...", min_values=1, max_values=1, options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PlaylistAddSongModal(self.cog, self.values[0]))

class PlaylistAddDropdownView(discord.ui.View):
    def __init__(self, cog, playlists):
        super().__init__(timeout=60)
        self.add_item(PlaylistAddDropdown(cog, playlists))

class PlaylistAddSongModal(discord.ui.Modal, title="Add Song to Playlist"):
    song = discord.ui.TextInput(label="Song name or YouTube URL", required=True)

    def __init__(self, cog, playlist_name):
        super().__init__()
        self.cog = cog
        self.playlist_name = playlist_name

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        name = self.playlist_name
        song = self.song.value
        if is_youtube_playlist_url(song):
            entries = await get_playlist_entries(song)
            if not entries:
                await interaction.response.send_message("No valid songs found in the playlist link.", ephemeral=True)
                return
            for entry in entries:
                PLAYLISTS[user_id][name].append({"query": entry["url"], "title": entry["title"]})
            save_playlists(PLAYLISTS)
            await interaction.response.send_message(f"Added {len(entries)} songs from playlist link to playlist '{name}'.", ephemeral=True)
            return
        title = await get_song_metadata(song)
        if not title:
            await interaction.response.send_message("Song not found.", ephemeral=True)
            return
        PLAYLISTS[user_id][name].append({"query": song, "title": title})
        save_playlists(PLAYLISTS)
        await interaction.response.send_message(f"Added '{title}' to playlist '{name}'.", ephemeral=True)

class PlaylistRemoveDropdown(discord.ui.Select):
    def __init__(self, cog, playlists):
        options = [discord.SelectOption(label=pl, value=pl) for pl in playlists]
        super().__init__(placeholder="Select a playlist...", min_values=1, max_values=1, options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        # Instead of dropdown for songs, show a modal for text input
        await interaction.response.send_modal(PlaylistRemoveSongModal(self.cog, self.values[0]))

class PlaylistRemoveDropdownView(discord.ui.View):
    def __init__(self, cog, playlists):
        super().__init__(timeout=60)
        self.add_item(PlaylistRemoveDropdown(cog, playlists))

class PlaylistRemoveSongModal(discord.ui.Modal, title="Remove Song from Playlist"):
    song = discord.ui.TextInput(label="Song name or position (number)", required=True)

    def __init__(self, cog, playlist_name):
        super().__init__()
        self.cog = cog
        self.playlist_name = playlist_name

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        name = self.playlist_name
        playlist = PLAYLISTS.get(user_id, {}).get(name, [])
        song_input = self.song.value.strip()

        if not playlist:
            await interaction.response.send_message("Playlist is empty.", ephemeral=True)
            return

        # If input is a number, treat as position
        if song_input.isdigit():
            idx = int(song_input) - 1
            if 0 <= idx < len(playlist):
                removed = playlist.pop(idx)
                save_playlists(PLAYLISTS)
                await interaction.response.send_message(f"Removed '{removed['title']}' from playlist '{name}'.", ephemeral=True)
                return
            else:
                await interaction.response.send_message("Invalid position.", ephemeral=True)
                return

        # Otherwise, fuzzy match song name
        from difflib import get_close_matches
        titles = [s['title'] for s in playlist]
        matches = get_close_matches(song_input, titles, n=1, cutoff=0.6)
        if matches:
            idx = titles.index(matches[0])
            removed = playlist.pop(idx)
            save_playlists(PLAYLISTS)
            await interaction.response.send_message(f"Removed '{removed['title']}' from playlist '{name}'.", ephemeral=True)
        else:
            await interaction.response.send_message("Song not found in playlist.", ephemeral=True)

class VolumeModal(discord.ui.Modal, title="Set Volume"):
    volume = discord.ui.TextInput(label="Volume (0-100)", required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(self.volume.value)
        except ValueError:
            await interaction.response.send_message("Please enter a valid number between 0 and 100.", ephemeral=True)
            return
        if not (0 <= value <= 100):
            await interaction.response.send_message("Volume must be between 0 and 100.", ephemeral=True)
            return
        await self.cog.volume.callback(self.cog, interaction, value)