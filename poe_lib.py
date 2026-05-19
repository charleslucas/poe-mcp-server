"""PoE API client and shared utilities for poe-mcp-server.

Provides:
  load_config()   — credentials from env vars or config.json
  PoeApi          — HTTP client for the PoE character-window API (POESESSID cookie auth)
  build_pob_xml   — stub (use pob-mcp lua_import_character instead)
  PobAnalyzer     — stub (use pob-mcp directly instead)

Env vars (set in .mcp.json):
  POE_SESSION_ID    — POESESSID cookie value
  POE_ACCOUNT_NAME  — account name, with or without discriminator (e.g. Account#1234)
  POE_CHARACTER_NAME — default character name (optional)

config.json keys (fallback, poe-mcp-server/ directory):
  poesessid, account, character, bandit, res_penalty
"""
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

_BASE_URL = "https://www.pathofexile.com/character-window"
_OAUTH_BASE = "https://api.pathofexile.com"
_HEADERS = {
    "Accept": "application/json",
}
_REQUEST_DELAY = 1.5   # minimum seconds between API calls (PoE rate limit)
_TIMEOUT = 30


def load_config() -> dict:
    """Load PoE credentials. Env vars take precedence over config.json."""
    cfg: dict = {}

    cfg_path = Path(__file__).parent / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if sessid := os.environ.get("POE_SESSION_ID"):
        cfg["poesessid"] = sessid
    if account := os.environ.get("POE_ACCOUNT_NAME"):
        cfg["account"] = account
    if char := os.environ.get("POE_CHARACTER_NAME"):
        cfg["character"] = char
    if email := os.environ.get("POE_CONTACT_EMAIL"):
        cfg["contact_email"] = email

    if not cfg.get("poesessid"):
        raise RuntimeError(
            "PoE session ID not found. Set POE_SESSION_ID env var "
            "or add 'poesessid' to poe-mcp-server/config.json."
        )
    if not cfg.get("account"):
        raise RuntimeError(
            "PoE account name not found. Set POE_ACCOUNT_NAME env var "
            "or add 'account' to poe-mcp-server/config.json."
        )

    return cfg


class PoeApi:
    """Thin HTTP client for the PoE character-window API using POESESSID auth."""

    _last_request_time: float = 0.0

    def __init__(self, sessid: str, account: str, character: str = "", contact_email: str = ""):
        self.sessid = sessid
        # Strip discriminator — the API uses only the base account name
        self.account = account.split("#")[0]
        self.character = character
        contact = f"; contact: {contact_email}" if contact_email else ""
        self.user_agent = f"poe-mcp-server/1.0 (account: {self.account}{contact})"

    def _get(self, endpoint: str, params: dict) -> dict:
        elapsed = time.monotonic() - PoeApi._last_request_time
        if elapsed < _REQUEST_DELAY:
            time.sleep(_REQUEST_DELAY - elapsed)

        url = f"{_BASE_URL}/{endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            **_HEADERS,
            "Cookie": f"POESESSID={self.sessid}",
        })
        try:
            req.add_header("User-Agent", self.user_agent)
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                PoeApi._last_request_time = time.monotonic()
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"PoE API {endpoint} returned HTTP {e.code}: {body}") from e

    def get_items(self, character_name: str = "") -> dict:
        """Fetch equipped items and character metadata.

        Returns dict with 'character' (name/class/level/league) and 'items' list.
        """
        return self._get("get-items", {
            "character": character_name or self.character,
            "accountName": self.account,
        })

    def get_passives(self, character_name: str = "") -> dict:
        """Fetch allocated passive tree nodes and mastery effects."""
        return self._get("get-passive-skills", {
            "character": character_name or self.character,
            "accountName": self.account,
        })

    def get_stash_tabs(self, league: str) -> dict:
        """Fetch stash tab list (metadata only, no items).

        Response includes 'tabs': list of {i, n, type, colour, ...}.
        """
        return self._get("get-stash-items", {
            "league": league,
            "tabs": 1,
            "tabIndex": 0,
            "accountName": self.account,
        })

    def get_stash_tab(self, league: str, tab_index: int) -> dict:
        """Fetch all items from one stash tab by index.

        Response includes 'items': list of PoE API item dicts.
        """
        return self._get("get-stash-items", {
            "league": league,
            "tabs": 0,
            "tabIndex": tab_index,
            "accountName": self.account,
        })


# ── Stubs for features handled by pob-mcp ────────────────────────────────────

def build_pob_xml(*args, **kwargs) -> str:
    raise NotImplementedError(
        "build_pob_xml is not implemented here. "
        "Use pob-mcp's lua_import_character tool to import a character into Path of Building."
    )


class PobAnalyzer:
    """Stub — headless PoB analysis is handled by pob-mcp."""

    def start(self):
        raise NotImplementedError("Use pob-mcp instead of PobAnalyzer.")

    def stop(self):
        pass

    def load_build(self, *args, **kwargs):
        raise NotImplementedError("Use pob-mcp instead of PobAnalyzer.")

    def eval_lua(self, *args, **kwargs):
        raise NotImplementedError("Use pob-mcp instead of PobAnalyzer.")
