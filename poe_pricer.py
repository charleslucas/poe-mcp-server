"""PoE Pricer MCP Server — live poe.ninja pricing.

For magic/rare items: uses the rare_scorer.py algorithm (if available).
For everything else (uniques, currency, gems, divination cards, etc.):
  queries poe.ninja live via exchange and stash API endpoints.

Tools:
  ninja_lookup — look up current poe.ninja price for any named item
  price_item   — price a single item (PoE API dict OR clipboard text)
  price_items  — price a batch of items (array of PoE API dicts)

Version: 2.0
"""
import importlib.util as _iutil
import json
import sys
import time
import urllib.request
from pathlib import Path

POE_MONITOR_DIR = Path(__file__).resolve().parent.parent / "buildstuff" / "poe_monitor"
sys.path.insert(0, str(POE_MONITOR_DIR))
_SCORER_PATH = POE_MONITOR_DIR / "rare_scorer.py"

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

app = Server("poe-pricer")

# ── poe.ninja live API ──────────────────────────────────────────────────────────

NINJA_EXCHANGE_URL = "https://poe.ninja/poe1/api/economy/exchange/current/overview"
NINJA_STASH_URL    = "https://poe.ninja/poe1/api/economy/stash/current/item/overview"
_NINJA_HEADERS     = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_NINJA_TTL         = 900  # 15 minutes

# Exchange endpoint types (bulk tradeable)
_EXCHANGE_TYPES = [
    "Currency", "Fragment", "DivinationCard", "Scarab", "Essence", "Oil",
    "Fossil", "Omen", "Tattoo", "AllflameEmber", "Artifact", "DeliriumOrb",
    "Astrolabe", "Resonator", "Wombgift", "Incubator",
]

# Stash endpoint types (equipment, gems, maps)
_STASH_TYPES = [
    "UniqueWeapon", "UniqueArmour", "UniqueAccessory", "UniqueFlask", "UniqueJewel",
    "ForbiddenJewel", "ShrineBelt", "UniqueTincture", "UniqueRelic",
    "SkillGem", "ClusterJewel",
    "Map", "BlightedMap", "BlightRavagedMap", "UniqueMap",
    "Invitation", "BaseType", "Beast", "Vial",
]

# Cache: key = (endpoint_url, league, type_name) → (timestamp, lines, items)
_cache: dict[tuple, tuple] = {}


def _fetch(url: str, league: str, type_name: str) -> tuple[list, list]:
    """Fetch from poe.ninja with caching. Returns (lines, items)."""
    key = (url, league, type_name)
    cached = _cache.get(key)
    if cached and (time.time() - cached[0]) < _NINJA_TTL:
        return cached[1], cached[2]
    try:
        full_url = f"{url}?league={urllib.parse.quote(league)}&type={type_name}"
        req = urllib.request.Request(full_url, headers=_NINJA_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        lines = data.get("lines", [])
        items = data.get("items", [])
        _cache[key] = (time.time(), lines, items)
        return lines, items
    except Exception:
        return [], []


import urllib.parse  # noqa: E402 (import after definition above)


def _ninja_lookup_live(query: str, league: str) -> list[dict]:
    """Search poe.ninja live for items matching the query name. Returns list of matches."""
    q = query.lower().strip()
    results = []

    # Search exchange endpoint (currencies, div cards, scarabs, etc.)
    for type_name in _EXCHANGE_TYPES:
        lines, items = _fetch(NINJA_EXCHANGE_URL, league, type_name)
        if not lines:
            continue
        id_to_name = {item["id"]: item["name"] for item in items}
        for line in lines:
            name = id_to_name.get(line.get("id", ""), "")
            if not name:
                continue
            if q in name.lower() or name.lower() == q:
                results.append({
                    "name": name,
                    "chaos_value": round(line.get("primaryValue", 0), 2),
                    "divine_value": None,
                    "listing_count": int(line.get("volumePrimaryValue", 0)),
                    "category": type_name,
                    "source": "exchange",
                })
                if name.lower() == q:
                    return results  # exact match — stop early

    # Search stash endpoint (uniques, gems, maps, etc.)
    for type_name in _STASH_TYPES:
        lines, _ = _fetch(NINJA_STASH_URL, league, type_name)
        if not lines:
            continue
        for line in lines:
            name = line.get("name", "")
            if not name:
                continue
            if q in name.lower() or name.lower() == q:
                results.append({
                    "name": name,
                    "chaos_value": round(line.get("chaosValue", 0), 2),
                    "divine_value": round(line.get("divineValue", 0), 2) if line.get("divineValue") else None,
                    "listing_count": line.get("listingCount", 0),
                    "category": type_name,
                    "variant": line.get("variant"),
                    "gem_level": line.get("gemLevel"),
                    "gem_quality": line.get("gemQuality"),
                    "links": line.get("links"),
                    "source": "stash",
                })
                if name.lower() == q:
                    return results  # exact match — stop early

    return results


def _best_ninja_price(name: str, league: str) -> dict | None:
    """Return the single best (highest-value) poe.ninja result for an exact name."""
    matches = [m for m in _ninja_lookup_live(name, league) if m["name"].lower() == name.lower()]
    if not matches:
        return None
    return max(matches, key=lambda m: m["chaos_value"])


# ── rare_scorer ─────────────────────────────────────────────────────────────────

def _scorer():
    """Load rare_scorer module. Raises if unavailable."""
    if not _SCORER_PATH.exists():
        raise FileNotFoundError(
            f"rare_scorer.py not found at {_SCORER_PATH}. "
            "Magic/rare item pricing requires the buildstuff poe_monitor module."
        )
    spec = _iutil.spec_from_file_location("rare_scorer", _SCORER_PATH)
    mod = _iutil.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Frame type constants ────────────────────────────────────────────────────────

FRAME_RARE   = 2
FRAME_MAGIC  = 1
ALGO_FRAMES  = {FRAME_MAGIC, FRAME_RARE}
NINJA_FRAMES = {3, 4, 5, 6, 9}

RARITY_MAP = {
    0: "Normal", 1: "Magic", 2: "Rare", 3: "Unique",
    4: "Gem", 5: "Currency", 6: "DivinationCard", 9: "Unique (Foil)",
}


def _price_single_api_item(item: dict, league: str = "Mirage") -> dict:
    """Price one PoE API item dict. Returns a result dict."""
    frame      = item.get("frameType", 0)
    name       = item.get("name", "").strip()
    type_line  = item.get("typeLine", "").strip()
    display    = f"{name} {type_line}".strip() if name else type_line
    ilvl       = item.get("ilvl", 0)

    if frame in ALGO_FRAMES:
        try:
            rs     = _scorer()
            result = rs.score_item(item)
            if result is None:
                return {"name": display, "ilvl": ilvl, "rarity": RARITY_MAP.get(frame, "?"),
                        "method": "algo", "price_estimate": 0, "note": "Could not score item"}
            out = {
                "name": result.name or display,
                "ilvl": result.ilvl,
                "rarity": RARITY_MAP.get(frame, "?"),
                "method": "algo",
                "category": result.category,
                "price_estimate": result.price_estimate,
                "total_score": result.total_score,
                "good_mods": result.good_mod_count,
                "junk_mods": result.junk_count,
                "breakdown": result.breakdown,
            }
            if result.is_fractured:
                out["fractured"] = True
                out["should_trade_check"] = result.should_trade_check
            return out
        except FileNotFoundError as e:
            return {"name": display, "ilvl": ilvl, "rarity": RARITY_MAP.get(frame, "?"),
                    "method": "unavailable", "price_estimate": None, "note": str(e)}

    # Named items — look up on poe.ninja live
    lookup_name = name if name else type_line
    ninja = _best_ninja_price(lookup_name, league)
    if ninja:
        out = {
            "name": display,
            "ilvl": ilvl,
            "rarity": RARITY_MAP.get(frame, "?"),
            "method": "ninja_live",
            "category": ninja["category"],
            "price_estimate": ninja["chaos_value"],
        }
        if ninja.get("divine_value"):
            out["price_divine"] = ninja["divine_value"]
        return out

    return {"name": display, "ilvl": ilvl, "rarity": RARITY_MAP.get(frame, "?"),
            "method": "not_found", "price_estimate": None,
            "note": f"'{lookup_name}' not found on poe.ninja"}


# ── Tool definitions ────────────────────────────────────────────────────────────

TOOLS = [
    Tool(
        name="ninja_lookup",
        description=(
            "Look up the current poe.ninja price for any named item — uniques, gems, "
            "currency, divination cards, scarabs, maps, and more. Fetches live from "
            "poe.ninja (cached 15 minutes). Supports partial name matching."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name":   {"type": "string", "description": "Item name to look up (partial match supported)."},
                "league": {"type": "string", "description": "League name (default: Mirage)."},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="price_item",
        description=(
            "Price a single item. Accepts either:\n"
            "  • item_dict: a PoE API item object (as returned by the stash or character API)\n"
            "  • item_text: raw clipboard text from PoE (Ctrl+C)\n"
            "Uniques, gems, currency, and divination cards are priced via live poe.ninja data. "
            "Magic/rare items use the local rare_scorer algorithm when available."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "item_dict": {"type": "object",  "description": "PoE API item dict."},
                "item_text": {"type": "string",  "description": "Raw item text copied from PoE (Ctrl+C format)."},
                "league":    {"type": "string",  "description": "League name (default: Mirage)."},
            },
        },
    ),
    Tool(
        name="price_items",
        description=(
            "Price a batch of items in one call. Accepts an array of PoE API item dicts. "
            "Returns results sorted by price (highest first). "
            "Uniques/gems/currency use live poe.ninja data; magic/rare use rare_scorer if available."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "items":           {"type": "array", "items": {"type": "object"}, "description": "Array of PoE API item dicts."},
                "min_price":       {"type": "number", "description": "Only include items with price_estimate >= this value (default 0)."},
                "include_unpriced":{"type": "boolean", "description": "Include items that couldn't be priced (default false)."},
                "league":          {"type": "string", "description": "League name (default: Mirage)."},
            },
            "required": ["items"],
        },
    ),
]


# ── Tool handler ─────────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        league = arguments.get("league", "Mirage")

        if name == "ninja_lookup":
            query   = arguments["name"]
            matches = _ninja_lookup_live(query, league)

            if not matches:
                return [TextContent(type="text", text=json.dumps(
                    {"name": query, "league": league, "note": "Not found on poe.ninja"}, indent=2))]

            # Deduplicate by name+variant, keep highest chaos value per entry
            seen = {}
            for m in matches:
                key = (m["name"], m.get("variant"))
                if key not in seen or m["chaos_value"] > seen[key]["chaos_value"]:
                    seen[key] = m

            results = sorted(seen.values(), key=lambda x: x["chaos_value"], reverse=True)[:10]
            return [TextContent(type="text", text=json.dumps(
                {"query": query, "league": league, "results": results}, indent=2))]

        elif name == "price_item":
            item_dict = arguments.get("item_dict")
            item_text = arguments.get("item_text")

            if item_dict:
                result = _price_single_api_item(item_dict, league)
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif item_text:
                # Parse item name from clipboard text
                lines = [l.strip() for l in item_text.strip().splitlines() if l.strip()]
                lookup_name = None
                for i, line in enumerate(lines):
                    if line.lower().startswith("rarity:"):
                        rarity = line.split(":", 1)[1].strip().lower()
                        if rarity in ("magic", "rare"):
                            # Try rare_scorer
                            try:
                                rs = _scorer()
                                algo_result = rs.score_item_text(item_text)
                                if algo_result is not None:
                                    out = {
                                        "name": algo_result.name,
                                        "ilvl": algo_result.ilvl,
                                        "method": "algo",
                                        "category": algo_result.category,
                                        "price_estimate": algo_result.price_estimate,
                                        "total_score": algo_result.total_score,
                                        "good_mods": algo_result.good_mod_count,
                                        "junk_mods": algo_result.junk_count,
                                        "breakdown": algo_result.breakdown,
                                    }
                                    return [TextContent(type="text", text=json.dumps(out, indent=2))]
                            except FileNotFoundError as e:
                                return [TextContent(type="text", text=json.dumps(
                                    {"note": str(e)}, indent=2))]
                        if i + 1 < len(lines):
                            lookup_name = lines[i + 1]
                        break

                if lookup_name:
                    ninja = _best_ninja_price(lookup_name, league)
                    if ninja:
                        return [TextContent(type="text", text=json.dumps({
                            "name": lookup_name, "method": "ninja_live",
                            "league": league,
                            "category": ninja["category"],
                            "price_estimate": ninja["chaos_value"],
                            "price_divine": ninja.get("divine_value"),
                        }, indent=2))]

                return [TextContent(type="text", text=json.dumps({
                    "note": "Could not price item. Not a magic/rare and not found on poe.ninja.",
                    "name": lookup_name, "league": league,
                }, indent=2))]

            else:
                return [TextContent(type="text", text="Error: provide item_dict or item_text")]

        elif name == "price_items":
            items            = arguments.get("items", [])
            min_price        = arguments.get("min_price", 0)
            include_unpriced = arguments.get("include_unpriced", False)

            results = []
            for item in items:
                r     = _price_single_api_item(item, league)
                price = r.get("price_estimate")
                if price is None:
                    if include_unpriced:
                        results.append(r)
                elif price >= min_price:
                    results.append(r)

            results.sort(key=lambda x: x.get("price_estimate") or 0, reverse=True)

            total_priced = sum(1 for r in results if r.get("price_estimate") is not None)
            total_value  = sum(r.get("price_estimate") or 0 for r in results)
            trade_check  = [r["name"] for r in results if r.get("should_trade_check")]

            out = {
                "total_items": len(items),
                "priced_count": total_priced,
                "total_value_chaos": round(total_value, 2),
                "league": league,
                "items": results,
            }
            if trade_check:
                out["should_trade_check"] = trade_check
            return [TextContent(type="text", text=json.dumps(out, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        import traceback
        return [TextContent(type="text", text=f"Error: {e}\n{traceback.format_exc()}")]


# ── Entry point ──────────────────────────────────────────────────────────────────

from mcp_server_utils import run_server

if __name__ == "__main__":
    run_server(app, port=8486, name="poe-pricer")
