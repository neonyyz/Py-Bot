import os
import json
from collections import deque

PERSISTENT_DIR = "persistent"
os.makedirs(PERSISTENT_DIR, exist_ok=True)
VOLUME_FILE = os.path.join(PERSISTENT_DIR, "guild_volumes.json")
PLAYLISTS_FILE = os.path.join(PERSISTENT_DIR, "playlists.json")

SONG_QUEUES = {}         # {guild_id: deque([(audio_url, title), ...])}
GUILD_VOLUMES = {}       # {guild_id: volume_float}
PLAYLISTS = {}           # {user_id: {playlist_name: [{"query": ..., "title": ...}, ...]}}

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
PLAYLISTS.update(load_playlists())
load_volumes()