"""Disk-based cache for PoE stash tab data.

Cache lives at ~/.cache/poe-mcp-server/{league}/
  tabs.json          — tab list (metadata)
  tab_{index}.json   — items for each fetched tab

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
    """Return seconds since file was last modified, or None if it doesn't exist."""
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


class StashCache:
    """Caching wrapper around PoeApi stash endpoints."""

    def __init__(self, api, league: str):
        self._api = api
        self.league = league

    def cache_age(self, tab_index: int) -> float | None:
        """Seconds since tab was last fetched, or None if not cached."""
        return _file_age(_cache_path(self.league, tab_index))

    def get_tab_list(self, force: bool = False) -> list[dict]:
        """Return list of tab dicts: [{i, n, type}, ...].

        Cached in tabs.json; re-fetched when forced or TTL expired.
        """
        p = _tab_list_path(self.league)
        age = _file_age(p)
        if not force and age is not None and age < _DEFAULT_TTL:
            return json.loads(p.read_text(encoding="utf-8"))

        data = self._api.get_stash_tabs(self.league)
        tabs = [
            {
                "i": t.get("i", idx),
                "n": t.get("n", ""),
                "type": t.get("type", "NormalStash"),
            }
            for idx, t in enumerate(data.get("tabs", []))
        ]
        p.write_text(json.dumps(tabs), encoding="utf-8")
        return tabs

    def get_tab(self, tab_index: int, force: bool = False) -> list[dict]:
        """Return items from a single tab. Fetches and caches on miss or force."""
        p = _cache_path(self.league, tab_index)
        age = _file_age(p)
        if not force and age is not None and age < _DEFAULT_TTL:
            return json.loads(p.read_text(encoding="utf-8"))

        data = self._api.get_stash_tab(self.league, tab_index)
        items = data.get("items", [])
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
        raise KeyError(f"Tab '{tab_name}' not found. Available tabs: {available}")

    def get_tabs(self, indices: Iterable[int], force: bool = False) -> list[dict]:
        """Return combined items from multiple tab indices, skipping failures."""
        items: list[dict] = []
        for i in indices:
            try:
                items.extend(self.get_tab(i, force=force))
            except Exception:
                pass
        return items
