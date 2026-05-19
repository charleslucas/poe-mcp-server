"""Disk-based cache for PoE stash tab data.

Cache lives at ~/.cache/poe-mcp-server/{league}/
  tabs.json          — tab list (metadata), normalized from either API format
  tab_{index}.json   — items for each fetched tab

Normalizes responses from both API versions:
  POESESSID / character-window:  {"tabs": [{i, n, type}], "items": [...]}
  OAuth / api.pathofexile.com:   {"stashes": [{id, name, type, index}]}
                                 {"stash": {id, name, type, items: [...]}}

The normalized tab list format is: [{i, n, type, id}]
  id is "" when using POESESSID (not needed for tab fetching by index).
  id is populated from OAuth and used for subsequent tab fetches.

Default TTL: 300 seconds (5 minutes). Force-refresh bypasses TTL.
"""
import json
import time
from pathlib import Path
from typing import Iterable

_CACHE_ROOT = Path.home() / ".cache" / "poe-mcp-server"
_DEFAULT_TTL = 300  # seconds


def _league_dir(league: str) -> Path:
    d = _CACHE_ROOT / league.lower().replace(" ", "_")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tab_list_path(league: str) -> Path:
    return _league_dir(league) / "tabs.json"


def _cache_path(league: str, tab_index: int) -> Path:
    return _league_dir(league) / f"tab_{tab_index}.json"


def _file_age(path: Path) -> float | None:
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def _normalize_tab_list(data: dict) -> list[dict]:
    """Normalize tab list from either API format to [{i, n, type, id}]."""
    if "stashes" in data:
        # OAuth api.pathofexile.com format
        return [
            {
                "i": t.get("index", idx),
                "n": t.get("name", ""),
                "type": t.get("type", "NormalStash"),
                "id": t.get("id", ""),
            }
            for idx, t in enumerate(data.get("stashes", []))
        ]
    else:
        # POESESSID character-window format
        return [
            {
                "i": t.get("i", idx),
                "n": t.get("n", ""),
                "type": t.get("type", "NormalStash"),
                "id": "",
            }
            for idx, t in enumerate(data.get("tabs", []))
        ]


def _normalize_tab_items(data: dict) -> list[dict]:
    """Normalize items from either API format."""
    if "stash" in data:
        # OAuth api.pathofexile.com format: {"stash": {"items": [...]}}
        return data["stash"].get("items", [])
    else:
        # POESESSID character-window format: {"items": [...]}
        return data.get("items", [])


class StashCache:
    """Caching wrapper around PoeApi stash endpoints."""

    def __init__(self, api, league: str):
        self._api = api
        self.league = league

    def cache_age(self, tab_index: int) -> float | None:
        """Seconds since tab was last fetched, or None if not cached."""
        return _file_age(_cache_path(self.league, tab_index))

    def get_tab_list(self, force: bool = False) -> list[dict]:
        """Return normalized list of tab dicts: [{i, n, type, id}].

        Cached in tabs.json; re-fetched when forced or TTL expired.
        """
        p = _tab_list_path(self.league)
        age = _file_age(p)
        if not force and age is not None and age < _DEFAULT_TTL:
            return json.loads(p.read_text(encoding="utf-8"))

        data = self._api.get_stash_tabs(self.league)
        tabs = _normalize_tab_list(data)
        p.write_text(json.dumps(tabs), encoding="utf-8")
        return tabs

    def get_tab(self, tab_index: int, force: bool = False) -> list[dict]:
        """Return items from a single tab. Fetches and caches on miss or force."""
        p = _cache_path(self.league, tab_index)
        age = _file_age(p)
        if not force and age is not None and age < _DEFAULT_TTL:
            return json.loads(p.read_text(encoding="utf-8"))

        # Look up stash_id for OAuth path (empty string → POESESSID fallback)
        stash_id = ""
        try:
            tabs = self.get_tab_list()
            for t in tabs:
                if t["i"] == tab_index:
                    stash_id = t.get("id", "")
                    break
        except Exception:
            pass

        data = self._api.get_stash_tab(self.league, tab_index, stash_id)
        items = _normalize_tab_items(data)
        p.write_text(json.dumps(items), encoding="utf-8")
        return items

    def get_tab_by_name(self, tab_name: str, force: bool = False) -> list[dict]:
        """Look up a tab by name (case-insensitive) and return its items."""
        tabs = self.get_tab_list()
        name_norm = tab_name.strip().lower()
        for t in tabs:
            if t.get("n", "").lower() == name_norm:
                return self.get_tab(t["i"], force=force)
        available = [t["n"] for t in tabs]
        raise KeyError(f"Tab '{tab_name}' not found. Available: {available}")

    def get_tabs(self, indices: Iterable[int], force: bool = False) -> list[dict]:
        """Return combined items from multiple tab indices, skipping failures."""
        items: list[dict] = []
        for i in indices:
            try:
                items.extend(self.get_tab(i, force=force))
            except Exception:
                pass
        return items
