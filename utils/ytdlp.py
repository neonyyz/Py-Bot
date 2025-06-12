import yt_dlp
import asyncio
import re

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

async def get_song_metadata(song_query: str):
    ydl_options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "extract_flat": "in_playlist",
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
    return bool(re.search(r"(youtube\.com|youtu\.be).*(list=)", query))

async def get_playlist_entries(playlist_url: str):
    ydl_options = {
        "extract_flat": True,
        "quiet": True,
        "skip_download": True,
        "force_generic_extractor": False,
    }
    results = await search_ytdlp_async(playlist_url, ydl_options)
    entries = results.get("entries", [])
    return [
        {
            "title": entry.get("title", "Unknown Title"),
            "url": f"https://www.youtube.com/watch?v={entry['id']}" if entry.get("id") else entry.get("url")
        }
        for entry in entries if entry.get("id") or entry.get("url")
    ]