# main.py

# RRQ / VCT Pacific Pistol Analysis Builder
#
# Builds:
# 1. vlr_stage1_match_links.csv
# 2. vlr_stage1_pistol_dataset.csv
# 3. vlr_stage1_roster_cache.json
#
# What it collects automatically:
# - Match links
# - Match ID
# - Match slug
# - Teams
# - Map names
# - Player names
# - Agent comps
# - Comp role counts
# - Comp category tags
# - Match URL
# - VOD URL per map if VLR exposes it
# - Round 1 pistol result
# - Round 13 pistol result
# - Attack pistol result
# - Defense pistol result
# - Overtime detection
# - Round 25 / OT pistol info if present
#
# Manual review later:
# - First contact time
# - FB / FD / 5v5 PP
#
# Usage:
# python main.py
# python main.py --resume

import argparse
import json
import os
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ==============================
# SETTINGS
# ==============================

BASE_URL = "https://www.vlr.gg"

DEFAULT_EVENT_URL = "https://www.vlr.gg/event/2775/vct-2026-pacific-stage-1/group-stage"

# Updated default collection targets.
# These are the two pages you gave me:
# - Group Stage: series_id=5374
# - Playoffs: series_id=5375
#
# The important new field is event_phase. It gets carried into:
#   vlr_stage1_match_links.csv
#   vlr_stage1_pistol_dataset.csv
#   rrq_pistol_round_level_dataset.csv, after analyze_pistol_dataset.py is updated
DEFAULT_EVENT_URLS = [
    {
        "event_phase": "Group Stage",
        "url": "https://www.vlr.gg/event/matches/2775/vct-2026-pacific-stage-1/?series_id=5374",
    },
    {
        "event_phase": "Playoffs",
        "url": "https://www.vlr.gg/event/matches/2775/vct-2026-pacific-stage-1/?series_id=5375",
    },
]

MATCH_LINKS_CSV = "vlr_stage1_match_links.csv"
OUTPUT_CSV = "vlr_stage1_pistol_dataset.csv"
ROSTER_CACHE_JSON = "vlr_stage1_roster_cache.json"

REQUEST_DELAY_SECONDS = 3

# True = safer/slower
# False = faster/higher chance of rate limit
USE_REQUEST_LIMITER = True

EVENT_SLUG_FILTER = "vct-2026-pacific-stage-1"
PLAYOFF_PHASE_TAGS = ["lr1", "lr2", "lr3", "ur1", "ur2", "ubsf", "ubf", "gf", "lf"]
PLAYOFF_PHASE_TAG_PATTERN = re.compile(
    r"(^|[^a-z0-9])(" + "|".join(PLAYOFF_PHASE_TAGS) + r")($|[^a-z0-9])",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

retry_strategy = Retry(
    total=3,
    backoff_factor=2,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)

adapter = HTTPAdapter(max_retries=retry_strategy)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)

_last_request_time = None


# ==============================
# AGENT ROLE LOOKUP
# ==============================

AGENT_ROLES = {
    # Duelists
    "Jett": "Duelist",
    "Raze": "Duelist",
    "Reyna": "Duelist",
    "Phoenix": "Duelist",
    "Yoru": "Duelist",
    "Neon": "Duelist",
    "Iso": "Duelist",
    "Waylay": "Duelist",

    # Controllers
    "Brimstone": "Controller",
    "Viper": "Controller",
    "Omen": "Controller",
    "Astra": "Controller",
    "Harbor": "Controller",
    "Clove": "Controller",

    # Initiators
    "Sova": "Initiator",
    "Breach": "Initiator",
    "Skye": "Initiator",
    "Kayo": "Initiator",
    "Fade": "Initiator",
    "Gekko": "Initiator",
    "Tejo": "Initiator",

    # Sentinels
    "Cypher": "Sentinel",
    "Killjoy": "Sentinel",
    "Sage": "Sentinel",
    "Chamber": "Sentinel",
    "Deadlock": "Sentinel",
    "Vyse": "Sentinel",
    "Veto": "Sentinel",
}


TEAM_NAME_CLEANUP = {
    "Gen G": "Gen.G",
    "Geng": "Gen.G",
    "Gen.G": "Gen.G",
    "Global Esports": "Global Esports",
    "T1": "T1",
    "Varrel": "VARREL",
    "Nongshim Redforce": "Nongshim RedForce",
    "Nongshim RedForce": "Nongshim RedForce",
    "Paper Rex": "Paper Rex",
    "Full Sense": "FULL SENSE",
    "Full SENSE": "FULL SENSE",
    "Detonation Focusme": "DetonatioN FocusMe",
    "Detonation FocusMe": "DetonatioN FocusMe",
    "Detonation Focusme": "DetonatioN FocusMe",
    "Team Secret": "Team Secret",
    "Drx": "DRX",
    "DRX": "DRX",
    "Kiwoom Drx": "DRX",
    "Kiwoom DRX": "DRX",
    "Rex Regum Qeon": "Rex Regum Qeon",
    "Zeta Division": "ZETA DIVISION",
    "ZETA Division": "ZETA DIVISION",
    "ZETA DIVISION": "ZETA DIVISION",
}


# ==============================
# BASIC HELPERS
# ==============================

def clean_url(url: str) -> str:
    """
    Removes query strings and fragments from VLR match/event URLs.

    Do NOT use this for YouTube VOD links because YouTube timestamp queries matter.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def is_match_url(url: str) -> bool:
    """
    VLR match URLs usually start with a number.
    """
    path = urlsplit(url).path
    return bool(re.match(r"^/\d+/", path))


def is_valid_stage1_match_url(url: str) -> bool:
    """
    Positive match filter for real VCT Pacific Stage 1 match URLs.

    This avoids the old fragile blocklist approach.
    """
    path = urlsplit(url).path.lower().strip("/")

    pattern = rf"^\d+/[^/]*{re.escape(EVENT_SLUG_FILTER)}"

    return bool(re.match(pattern, path))


def polite_delay_if_needed():
    """
    Waits only when needed and avoids printing delay noise on the first request.
    """
    global _last_request_time

    if not USE_REQUEST_LIMITER:
        return

    if _last_request_time is None:
        return

    elapsed = time.time() - _last_request_time
    remaining = REQUEST_DELAY_SECONDS - elapsed

    if remaining > 0:
        print(f"Waiting {remaining:.1f} seconds before request...")
        time.sleep(remaining)


def get_soup(url: str, label: str = "page") -> BeautifulSoup:
    """
    Downloads a page and returns BeautifulSoup parser.
    """
    global _last_request_time

    polite_delay_if_needed()

    print(f"Reading {label}: {url}")
    response = SESSION.get(url, timeout=25)

    _last_request_time = time.time()

    print(f"Status code: {response.status_code}")

    response.raise_for_status()

    return BeautifulSoup(response.text, "lxml")


def normalize_agent_name(name: str) -> str:
    if not name:
        return ""

    name = str(name).strip()

    if name.upper() == "KAY/O":
        return "Kayo"

    return name.title()


def normalize_map_name(name: str) -> str:
    if not name:
        return ""

    return str(name).strip().title()


def normalize_team_name(name: str) -> str:
    if not name:
        return ""

    name = re.sub(r"\s+", " ", str(name)).strip()

    # Preserve known stylized names if already cleaned.
    if name in TEAM_NAME_CLEANUP:
        return TEAM_NAME_CLEANUP[name]

    title_name = name.title()

    return TEAM_NAME_CLEANUP.get(title_name, title_name)


def normalize_player_name(name: str) -> str:
    if not name:
        return ""

    name = str(name)
    name = re.sub(r"\s+", " ", name).strip()

    return name


def extract_match_id_from_url(match_url: str) -> str:
    match = re.search(r"vlr\.gg/(\d+)/", match_url)

    if match:
        return match.group(1)

    path_parts = urlsplit(match_url).path.strip("/").split("/")
    return path_parts[0] if path_parts else ""


def extract_match_slug_from_url(match_url: str) -> str:
    path_parts = urlsplit(match_url).path.strip("/").split("/")
    return path_parts[1] if len(path_parts) > 1 else ""


def has_playoff_phase_tag(value) -> bool:
    text = str(value).strip().lower()
    if not text or text == "nan":
        return False
    return PLAYOFF_PHASE_TAG_PATTERN.search(text) is not None


def infer_event_phase(event_phase, match_slug="", match_url="", event="") -> str:
    if any(has_playoff_phase_tag(value) for value in [event_phase, match_slug, match_url, event]):
        return "Playoffs"

    phase = str(event_phase).strip()
    phase_lower = phase.lower()

    if phase_lower in ["playoffs", "playoff", "bracket"]:
        return "Playoffs"
    if phase_lower in ["group stage", "groups", "group", "swiss"]:
        return "Group Stage"
    if phase and phase_lower != "nan":
        return phase
    return "Group Stage"


def extract_match_teams_from_slug(match_url: str) -> tuple[str, str]:
    slug = extract_match_slug_from_url(match_url).lower()

    slug = re.sub(r"-vct-2026-pacific-stage-1-w\d+$", "", slug)
    slug = re.sub(r"-vct-2026-pacific-stage-1-[a-z0-9]+$", "", slug)

    if "-vs-" not in slug:
        return "", ""

    team_1_slug, team_2_slug = slug.split("-vs-", 1)

    def clean_team_name(team_slug: str) -> str:
        raw = team_slug.replace("-", " ").title()
        return normalize_team_name(raw)

    return clean_team_name(team_1_slug), clean_team_name(team_2_slug)


def bool_to_text(value: bool) -> str:
    return "True" if value else "False"


# ==============================
# ROSTER CACHE HELPERS
# ==============================

def load_roster_cache() -> dict:
    if not os.path.exists(ROSTER_CACHE_JSON):
        return {}

    try:
        with open(ROSTER_CACHE_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_roster_cache(roster_cache: dict):
    with open(ROSTER_CACHE_JSON, "w", encoding="utf-8") as f:
        json.dump(roster_cache, f, indent=4, ensure_ascii=False)


def update_roster_cache_for_match(
    roster_cache: dict,
    match_id: str,
    match_url: str,
    team_1: str,
    team_2: str,
    team_1_players: list[str],
    team_2_players: list[str],
) -> dict:
    if match_id not in roster_cache:
        roster_cache[match_id] = {
            "match_url": match_url,
            "teams": {},
        }

    roster_cache[match_id]["match_url"] = match_url
    roster_cache[match_id]["teams"][team_1] = team_1_players
    roster_cache[match_id]["teams"][team_2] = team_2_players

    return roster_cache


# ==============================
# STEP 1: MATCH LINK COLLECTION
# ==============================

def get_match_links_from_event(event_url: str) -> list[str]:
    """
    Gets match links from a VLR event/matches page.

    IMPORTANT:
    Do NOT clean the event_url here because the series_id query string matters.

    Example:
    - ?series_id=5374 = Group Stage
    - ?series_id=5375 = Playoffs

    We only clean the individual match URLs after extracting them.
    """
    event_url = str(event_url).strip()

    soup = get_soup(event_url, label="event page")

    links = set()

    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()

        if not href:
            continue

        full_url = clean_url(urljoin(BASE_URL, href))

        if is_valid_stage1_match_url(full_url):
            links.add(full_url)

    return sorted(links, key=lambda x: int(extract_match_id_from_url(x) or 0))


def make_match_link_dataframe(match_link_rows: list[dict]) -> pd.DataFrame:
    """
    Builds a match-link dataframe.

    Updated: carries event_phase forward so the later dashboard can split:
      - Group Stage
      - Playoffs
      - Both
    """
    rows = []
    seen_urls = set()

    for item in match_link_rows:
        if isinstance(item, str):
            url = item
            event_phase = ""
        else:
            url = str(item.get("match_url", "")).strip()
            event_phase = str(item.get("event_phase", "")).strip()

        if not url:
            continue

        # Avoid duplicates if the same match appears from more than one page.
        if url in seen_urls:
            continue

        seen_urls.add(url)

        rows.append(
            {
                "event": "VCT 2026 Pacific Stage 1",
                "event_phase": event_phase,
                "match_id": extract_match_id_from_url(url),
                "match_slug": extract_match_slug_from_url(url),
                "match_url": url,
            }
        )

    return pd.DataFrame(rows)


def build_match_links_csv(event_targets) -> pd.DataFrame:
    """
    Builds vlr_stage1_match_links.csv.

    event_targets can be either:
      1. A single URL string
      2. A list of dicts like:
         {"event_phase": "Group Stage", "url": "https://..."}

    By default, main() passes both Group Stage and Playoffs.
    """
    print("=" * 80)
    print("STEP 1: BUILDING MATCH LINKS CSV")
    print("=" * 80)

    if isinstance(event_targets, str):
        event_targets = [
            {
                "event_phase": "Custom",
                "url": event_targets,
            }
        ]

    all_match_link_rows = []

    for target in event_targets:
        event_phase = str(target.get("event_phase", "")).strip()
        event_url = str(target.get("url", "")).strip()

        if not event_url:
            continue

        print()
        print(f"Collecting phase: {event_phase or 'Unknown'}")
        print(f"Event page: {event_url}")

        match_links = get_match_links_from_event(event_url)

        print(f"Found {len(match_links)} match link(s) for {event_phase or 'Unknown'}.")

        for match_url in match_links:
            all_match_link_rows.append(
                {
                    "event_phase": event_phase,
                    "match_url": match_url,
                }
            )

    print()
    print(f"Found {len(all_match_link_rows)} raw VCT Pacific Stage 1 match link row(s).")

    match_df = make_match_link_dataframe(all_match_link_rows)

    print(f"After duplicate removal: {len(match_df)} unique match link(s).")

    if not match_df.empty and "event_phase" in match_df.columns:
        print()
        print("Match links by phase:")
        print(match_df["event_phase"].value_counts(dropna=False))

    match_df.to_csv(MATCH_LINKS_CSV, index=False, encoding="utf-8-sig")

    print()
    print(f"Saved match links to: {MATCH_LINKS_CSV}")
    print()

    return match_df


# ==============================
# TEAM NAME PARSING
# ==============================

def extract_match_teams(soup: BeautifulSoup, match_url: str) -> tuple[str, str]:
    possible_selectors = [
        ".match-header-vs-team-name",
        ".match-header-vs-team .wf-title-med",
        ".match-header-vs-team-name .text-of",
        ".match-header-link-name .wf-title-med",
    ]

    for selector in possible_selectors:
        team_nodes = soup.select(selector)

        team_names = []

        for node in team_nodes:
            team_name = node.get_text(" ", strip=True)
            team_name = re.sub(r"\s+", " ", team_name).strip()

            if team_name:
                team_names.append(normalize_team_name(team_name))

        # Keep unique order.
        unique_team_names = []
        for team in team_names:
            if team not in unique_team_names:
                unique_team_names.append(team)

        if len(unique_team_names) >= 2:
            return unique_team_names[0], unique_team_names[1]

    return extract_match_teams_from_slug(match_url)


# ==============================
# MAP / VOD PARSING
# ==============================

def parse_map_nav_items(soup: BeautifulSoup) -> list[dict]:
    map_items = []

    for item in soup.select(".vm-stats-gamesnav-item.js-map-switch[data-href]"):
        data_href = item.get("data-href", "").strip()
        data_disabled = item.get("data-disabled", "").strip()
        game_id = item.get("data-game-id", "").strip()

        if not data_href:
            continue

        if data_disabled == "1":
            continue

        full_map_url = urljoin(BASE_URL, data_href)

        if "map=all" in full_map_url.lower():
            continue

        map_match = re.search(r"[?&]map=(\d+)", full_map_url)

        if map_match:
            map_number = int(map_match.group(1))
        else:
            map_number = len(map_items) + 1

        text = item.get_text(" ", strip=True)

        map_name = re.sub(r"^\d+\s*", "", text).strip()
        map_name = normalize_map_name(map_name)

        if not map_name:
            continue

        if map_name.lower() in {"all maps", "tbd"}:
            continue

        map_items.append(
            {
                "map_number": map_number,
                "map_name": map_name,
                "game_id": game_id,
                "map_url": full_map_url,
            }
        )

    map_items.sort(key=lambda x: x["map_number"])

    return map_items


def maybe_extract_map_number_from_label(text: str):
    if not text:
        return None

    match = re.search(r"\bMap\s*(\d+)\b", text, re.IGNORECASE)

    if not match:
        return None

    return int(match.group(1))


def is_vod_url(url: str) -> bool:
    lower = url.lower()
    return (
        "youtube.com" in lower
        or "youtu.be" in lower
        or "twitch.tv" in lower
        or "vod" in lower
    )


def extract_vod_links_by_map(soup: BeautifulSoup) -> dict:
    """
    Extracts map-specific VOD links.

    Primary:
    - div.match-vods

    Fallback:
    - scans all links for YouTube/Twitch/VOD URLs with labels like "Map 1".
    """

    vod_links = {}

    # Primary VLR container.
    vod_section = soup.select_one("div.match-vods")

    if vod_section:
        for a_tag in vod_section.select("a[href]"):
            text = a_tag.get_text(" ", strip=True)
            href = a_tag.get("href", "").strip()

            if not text or not href:
                continue

            map_number = maybe_extract_map_number_from_label(text)

            if map_number is None:
                continue

            # Do NOT clean YouTube URLs. The ?t= timestamp matters.
            full_url = urljoin(BASE_URL, href)

            vod_links[map_number] = full_url

    # Fallback scan if the container changes or misses links.
    for a_tag in soup.select("a[href]"):
        text = a_tag.get_text(" ", strip=True)
        href = a_tag.get("href", "").strip()

        if not text or not href:
            continue

        map_number = maybe_extract_map_number_from_label(text)

        if map_number is None:
            continue

        full_url = urljoin(BASE_URL, href)

        if not is_vod_url(full_url):
            continue

        if map_number not in vod_links:
            vod_links[map_number] = full_url

    return vod_links


def find_game_block(soup: BeautifulSoup, game_id: str):
    if not game_id:
        return None

    return soup.select_one(f'div.vm-stats-game[data-game-id="{game_id}"]')


# ==============================
# PLAYER + AGENT COMP PARSING
# ==============================

def extract_player_name_from_row(row) -> str:
    player_cell = (
        row.select_one("td.mod-player")
        or row.select_one(".mod-player")
        or row.select_one("td:first-child")
    )

    if not player_cell:
        return ""

    name_node = (
        player_cell.select_one(".text-of")
        or player_cell.select_one("a")
    )

    if name_node:
        name = name_node.get_text(" ", strip=True)
    else:
        name = player_cell.get_text(" ", strip=True)

    name = re.sub(r"\s+", " ", name).strip()

    # Avoid giant stat-cell text.
    if len(name) > 30:
        name = name.split(" ")[0].strip()

    return normalize_player_name(name)


def extract_agent_name_from_row(row) -> str:
    agent_img = row.select_one('td.mod-agents img[src*="/img/vlr/game/agents/"]')

    if not agent_img:
        agent_img = row.select_one('img[src*="/img/vlr/game/agents/"]')

    if not agent_img:
        return ""

    raw_agent_name = (
        agent_img.get("title")
        or agent_img.get("alt")
        or ""
    )

    return normalize_agent_name(raw_agent_name)


def extract_team_player_agent_pairs_from_table(table) -> list[dict]:
    """
    Returns:
    [
        {"player": "Jemkin", "agent": "Waylay"},
        ...
    ]
    """

    pairs = []

    rows = table.select("tbody > tr")

    for row in rows:
        agent_name = extract_agent_name_from_row(row)

        if not agent_name:
            continue

        player_name = extract_player_name_from_row(row)

        pairs.append(
            {
                "player": player_name,
                "agent": agent_name,
            }
        )

    return pairs


def get_agent_role(agent_name: str) -> str:
    return AGENT_ROLES.get(agent_name, "Unknown")


def categorize_comp(agents: list[str]) -> dict:
    role_counts = {
        "Duelist": 0,
        "Controller": 0,
        "Initiator": 0,
        "Sentinel": 0,
        "Unknown": 0,
    }

    for agent in agents:
        role = get_agent_role(agent)
        role_counts[role] = role_counts.get(role, 0) + 1

    tags = []

    if role_counts["Duelist"] == 0:
        tags.append("No Duelist")
    elif role_counts["Duelist"] == 1:
        tags.append("Solo Duelist")
    elif role_counts["Duelist"] >= 2:
        tags.append("Double Duelist")

    if role_counts["Controller"] >= 2:
        tags.append("Double Controller")

    if role_counts["Initiator"] >= 2:
        tags.append("Double Initiator")

    if role_counts["Sentinel"] >= 2:
        tags.append("Double Sentinel")

    if role_counts["Duelist"] >= 2 and role_counts["Controller"] >= 2:
        tags.append("Double Duelist + Double Controller")

    if role_counts["Duelist"] >= 2 and role_counts["Initiator"] >= 2:
        tags.append("Double Duelist + Double Initiator")

    if role_counts["Controller"] >= 2 and role_counts["Initiator"] >= 2:
        tags.append("Double Controller + Double Initiator")

    if not tags:
        tags.append("Standard")

    return {
        "duelist_count": role_counts["Duelist"],
        "controller_count": role_counts["Controller"],
        "initiator_count": role_counts["Initiator"],
        "sentinel_count": role_counts["Sentinel"],
        "unknown_role_count": role_counts["Unknown"],
        "double_duelist": role_counts["Duelist"] >= 2,
        "double_controller": role_counts["Controller"] >= 2,
        "double_initiator": role_counts["Initiator"] >= 2,
        "double_sentinel": role_counts["Sentinel"] >= 2,
        "comp_tags": " | ".join(tags),
    }


# ==============================
# PISTOL ROUND PARSING
# ==============================

def class_text(node) -> str:
    """
    Turns a BeautifulSoup node's class list into one lowercase string.
    """
    if not node:
        return ""

    return " ".join(node.get("class", [])).lower()


def infer_side_from_round_square(square) -> str:
    """
    VLR uses:
    mod-t  = attack side
    mod-ct = defense side
    """

    classes = class_text(square)

    if "mod-ct" in classes:
        return "Defense"

    if "mod-t" in classes:
        return "Attack"

    return ""


def square_is_win(square) -> bool:
    """
    A round square is won if it has the mod-win class.
    """
    return "mod-win" in class_text(square)


def parse_round_column(round_col, team_1: str, team_2: str) -> dict | None:
    """
    Parses one VLR round column.

    Each normal round has two .rnd-sq squares:
    square 0 = top team
    square 1 = bottom team
    """

    round_num_node = round_col.select_one(".rnd-num")

    if not round_num_node:
        return None

    round_num_text = round_num_node.get_text(strip=True)

    if not round_num_text.isdigit():
        return None

    round_number = int(round_num_text)

    squares = round_col.select(".rnd-sq")

    if len(squares) < 2:
        return None

    team_1_square = squares[0]
    team_2_square = squares[1]

    team_1_win = square_is_win(team_1_square)
    team_2_win = square_is_win(team_2_square)

    team_1_side = infer_side_from_round_square(team_1_square)
    team_2_side = infer_side_from_round_square(team_2_square)

    # Sometimes only the winning square has side class.
    # Infer the opposite side when possible.
    if team_1_side == "Attack" and not team_2_side:
        team_2_side = "Defense"
    elif team_1_side == "Defense" and not team_2_side:
        team_2_side = "Attack"

    if team_2_side == "Attack" and not team_1_side:
        team_1_side = "Defense"
    elif team_2_side == "Defense" and not team_1_side:
        team_1_side = "Attack"

    winner = ""

    if team_1_win and not team_2_win:
        winner = team_1
    elif team_2_win and not team_1_win:
        winner = team_2

    return {
        "round_number": round_number,
        "winner": winner,
        "team_1_result": "Win" if winner == team_1 else ("Loss" if winner == team_2 else ""),
        "team_2_result": "Win" if winner == team_2 else ("Loss" if winner == team_1 else ""),
        "team_1_side": team_1_side,
        "team_2_side": team_2_side,
    }


def extract_rounds_from_game_block(game_block, team_1: str, team_2: str) -> dict[int, dict]:
    parsed_rounds = {}

    rounds_container = game_block.select_one(".vlr-rounds")

    if not rounds_container:
        return parsed_rounds

    round_cols = rounds_container.select(".vlr-rounds-row-col")

    for round_col in round_cols:
        if "mod-spacing" in class_text(round_col):
            continue

        parsed = parse_round_column(round_col, team_1, team_2)

        if not parsed:
            continue

        parsed_rounds[parsed["round_number"]] = parsed

    return parsed_rounds


def blank_pistol_data() -> dict:
    return {
        "round_1_winner": "",
        "round_13_winner": "",
        "round_25_winner": "",

        "team_1_round_1_result": "",
        "team_2_round_1_result": "",
        "team_1_round_13_result": "",
        "team_2_round_13_result": "",
        "team_1_round_25_result": "",
        "team_2_round_25_result": "",

        "team_1_round_1_side": "",
        "team_2_round_1_side": "",
        "team_1_round_13_side": "",
        "team_2_round_13_side": "",
        "team_1_round_25_side": "",
        "team_2_round_25_side": "",

        "overtime_detected": False,
        "max_round_number": "",
        "pistol_parse_status": "not_found",
    }


def extract_pistol_rounds(game_block, team_1: str, team_2: str) -> dict:
    """
    Extracts Round 1 and Round 13 pistol results from VLR round history.

    Note:
    Round 13 remains the second-half pistol.
    If overtime exists, Round 25 is the first overtime pistol and is recorded separately.
    """

    result = blank_pistol_data()

    rounds_container = game_block.select_one(".vlr-rounds")

    if not rounds_container:
        result["pistol_parse_status"] = "no_vlr_rounds_container"
        return result

    parsed_rounds = extract_rounds_from_game_block(game_block, team_1, team_2)

    if not parsed_rounds:
        result["pistol_parse_status"] = "rounds_container_but_no_rounds_parsed"
        return result

    max_round = max(parsed_rounds.keys())
    overtime_detected = max_round > 24

    result["max_round_number"] = max_round
    result["overtime_detected"] = overtime_detected

    round_1 = parsed_rounds.get(1)
    round_13 = parsed_rounds.get(13)
    round_25 = parsed_rounds.get(25)

    if round_1:
        result["round_1_winner"] = round_1["winner"]
        result["team_1_round_1_result"] = round_1["team_1_result"]
        result["team_2_round_1_result"] = round_1["team_2_result"]
        result["team_1_round_1_side"] = round_1["team_1_side"]
        result["team_2_round_1_side"] = round_1["team_2_side"]

    if round_13:
        result["round_13_winner"] = round_13["winner"]
        result["team_1_round_13_result"] = round_13["team_1_result"]
        result["team_2_round_13_result"] = round_13["team_2_result"]
        result["team_1_round_13_side"] = round_13["team_1_side"]
        result["team_2_round_13_side"] = round_13["team_2_side"]

    if round_25:
        result["round_25_winner"] = round_25["winner"]
        result["team_1_round_25_result"] = round_25["team_1_result"]
        result["team_2_round_25_result"] = round_25["team_2_result"]
        result["team_1_round_25_side"] = round_25["team_1_side"]
        result["team_2_round_25_side"] = round_25["team_2_side"]

    if round_1 and round_13:
        result["pistol_parse_status"] = "parsed_round_1_and_13"
    elif round_1:
        result["pistol_parse_status"] = "parsed_round_1_only"
    elif round_13:
        result["pistol_parse_status"] = "parsed_round_13_only"
    else:
        result["pistol_parse_status"] = f"rounds_found_but_no_pistols_parsed_{len(parsed_rounds)}"

    if overtime_detected:
        result["pistol_parse_status"] += "_with_overtime"

    return result


def team_specific_pistol_fields(pistol_data: dict, team_number: int) -> dict:
    """
    Converts map-level pistol data into row-specific values.

    team_number 1 = top team on VLR
    team_number 2 = bottom team on VLR
    """

    if team_number == 1:
        round_1_result = pistol_data["team_1_round_1_result"]
        round_13_result = pistol_data["team_1_round_13_result"]
        round_25_result = pistol_data["team_1_round_25_result"]

        round_1_side = pistol_data["team_1_round_1_side"]
        round_13_side = pistol_data["team_1_round_13_side"]
        round_25_side = pistol_data["team_1_round_25_side"]
    else:
        round_1_result = pistol_data["team_2_round_1_result"]
        round_13_result = pistol_data["team_2_round_13_result"]
        round_25_result = pistol_data["team_2_round_25_result"]

        round_1_side = pistol_data["team_2_round_1_side"]
        round_13_side = pistol_data["team_2_round_13_side"]
        round_25_side = pistol_data["team_2_round_25_side"]

    attack_pistol_result = ""
    defense_pistol_result = ""

    if round_1_side == "Attack":
        attack_pistol_result = round_1_result
    elif round_1_side == "Defense":
        defense_pistol_result = round_1_result

    if round_13_side == "Attack":
        attack_pistol_result = round_13_result
    elif round_13_side == "Defense":
        defense_pistol_result = round_13_result

    return {
        "round_1_result": round_1_result,
        "round_13_result": round_13_result,
        "round_25_result": round_25_result,

        "round_1_side": round_1_side,
        "round_13_side": round_13_side,
        "round_25_side": round_25_side,

        "attack_pistol_result": attack_pistol_result,
        "defense_pistol_result": defense_pistol_result,
    }


# ==============================
# ROW BUILDING
# ==============================

def build_team_map_row(
    match_id: str,
    match_url: str,
    match_slug: str,
    vod_url: str,
    map_number: int,
    map_name: str,
    team: str,
    opponent: str,
    team_number: int,
    player_agent_pairs: list[dict],
    pistol_data: dict,
) -> dict:
    players = [pair.get("player", "") for pair in player_agent_pairs]
    agents = [pair.get("agent", "") for pair in player_agent_pairs]

    comp_info = categorize_comp(agents)
    pistol_fields = team_specific_pistol_fields(pistol_data, team_number)

    row = {
        "event": "VCT 2026 Pacific Stage 1",
        "match_id": match_id,
        "match_slug": match_slug,
        "map_number": map_number,
        "map_played": map_name,
        "team": team,
        "opponent": opponent,
        "team_number": team_number,

        "match_url": match_url,
        "vod_url": vod_url,

        "players": ", ".join(players),
        "agents": ", ".join(agents),
        "player_agent_pairs": " | ".join(
            [
                f"{pair.get('player', '')}:{pair.get('agent', '')}"
                for pair in player_agent_pairs
            ]
        ),

        "player_1": players[0] if len(players) > 0 else "",
        "player_2": players[1] if len(players) > 1 else "",
        "player_3": players[2] if len(players) > 2 else "",
        "player_4": players[3] if len(players) > 3 else "",
        "player_5": players[4] if len(players) > 4 else "",

        "agent_1": agents[0] if len(agents) > 0 else "",
        "agent_2": agents[1] if len(agents) > 1 else "",
        "agent_3": agents[2] if len(agents) > 2 else "",
        "agent_4": agents[3] if len(agents) > 3 else "",
        "agent_5": agents[4] if len(agents) > 4 else "",

        "comp_tags": comp_info["comp_tags"],

        "round_1_winner": pistol_data["round_1_winner"],
        "round_13_winner": pistol_data["round_13_winner"],
        "round_25_winner": pistol_data["round_25_winner"],

        "round_1_result": pistol_fields["round_1_result"],
        "round_13_result": pistol_fields["round_13_result"],
        "round_25_result": pistol_fields["round_25_result"],

        "round_1_side": pistol_fields["round_1_side"],
        "round_13_side": pistol_fields["round_13_side"],
        "round_25_side": pistol_fields["round_25_side"],

        "attack_pistol_result": pistol_fields["attack_pistol_result"],
        "defense_pistol_result": pistol_fields["defense_pistol_result"],

        "overtime_detected": bool_to_text(pistol_data["overtime_detected"]),
        "max_round_number": pistol_data["max_round_number"],
        "pistol_parse_status": pistol_data["pistol_parse_status"],

        # Role-count columns are placed near the end in the Excel workbook.
        "duelist_count": comp_info["duelist_count"],
        "controller_count": comp_info["controller_count"],
        "initiator_count": comp_info["initiator_count"],
        "sentinel_count": comp_info["sentinel_count"],
        "unknown_role_count": comp_info["unknown_role_count"],
        "double_duelist": comp_info["double_duelist"],
        "double_controller": comp_info["double_controller"],
        "double_initiator": comp_info["double_initiator"],
        "double_sentinel": comp_info["double_sentinel"],
    }

    return row


def extract_map_rows_from_match(
    soup: BeautifulSoup,
    match_url: str,
    match_id: str,
    match_slug: str,
    team_1: str,
    team_2: str,
    map_item: dict,
    vod_links_by_map: dict,
    roster_cache: dict,
) -> tuple[list[dict], dict]:
    rows = []

    map_number = map_item["map_number"]
    map_name = map_item["map_name"]
    game_id = map_item["game_id"]

    vod_url = vod_links_by_map.get(map_number, "")

    game_block = find_game_block(soup, game_id)

    if not game_block:
        print(f"  Could not find game block for {map_name} | game_id={game_id}")
        return rows, roster_cache

    tables = game_block.select("table.wf-table-inset.mod-overview")[:2]

    if len(tables) < 2:
        print(f"  Could not find both overview tables for {map_name}")
        return rows, roster_cache

    team_1_pairs = extract_team_player_agent_pairs_from_table(tables[0])
    team_2_pairs = extract_team_player_agent_pairs_from_table(tables[1])

    team_1_players = [pair.get("player", "") for pair in team_1_pairs if pair.get("player", "")]
    team_2_players = [pair.get("player", "") for pair in team_2_pairs if pair.get("player", "")]

    roster_cache = update_roster_cache_for_match(
        roster_cache=roster_cache,
        match_id=match_id,
        match_url=match_url,
        team_1=team_1,
        team_2=team_2,
        team_1_players=team_1_players,
        team_2_players=team_2_players,
    )

    pistol_data = extract_pistol_rounds(game_block, team_1, team_2)

    if not team_1_pairs:
        print(f"  No player/agent pairs found for {team_1} on {map_name}")

    if not team_2_pairs:
        print(f"  No player/agent pairs found for {team_2} on {map_name}")

    rows.append(
        build_team_map_row(
            match_id=match_id,
            match_url=match_url,
            match_slug=match_slug,
            vod_url=vod_url,
            map_number=map_number,
            map_name=map_name,
            team=team_1,
            opponent=team_2,
            team_number=1,
            player_agent_pairs=team_1_pairs,
            pistol_data=pistol_data,
        )
    )

    rows.append(
        build_team_map_row(
            match_id=match_id,
            match_url=match_url,
            match_slug=match_slug,
            vod_url=vod_url,
            map_number=map_number,
            map_name=map_name,
            team=team_2,
            opponent=team_1,
            team_number=2,
            player_agent_pairs=team_2_pairs,
            pistol_data=pistol_data,
        )
    )

    print(
        f"  {map_name}: extracted comps | "
        f"R1: {pistol_data['round_1_winner'] or 'blank'} | "
        f"R13: {pistol_data['round_13_winner'] or 'blank'} | "
        f"OT: {pistol_data['overtime_detected']} | "
        f"status: {pistol_data['pistol_parse_status']} | "
        f"VOD URL: {vod_url or 'blank'}"
    )

    return rows, roster_cache


# ==============================
# DATASET BUILDER
# ==============================

def get_processed_match_ids_from_existing_output() -> set[str]:
    if not os.path.exists(OUTPUT_CSV):
        return set()

    try:
        existing_df = pd.read_csv(OUTPUT_CSV, dtype={"match_id": str})
    except Exception:
        return set()

    if "match_id" not in existing_df.columns:
        return set()

    return set(existing_df["match_id"].dropna().astype(str).unique().tolist())


def load_existing_output_rows() -> list[dict]:
    if not os.path.exists(OUTPUT_CSV):
        return []

    try:
        existing_df = pd.read_csv(OUTPUT_CSV, dtype={"match_id": str})
    except Exception:
        return []

    if "event_phase" not in existing_df.columns:
        existing_df["event_phase"] = ""
    else:
        existing_df["event_phase"] = (
            existing_df["event_phase"]
            .fillna("")
            .astype(str)
            .str.strip()
        )

    existing_df["event_phase"] = existing_df.apply(
        lambda row: infer_event_phase(
            row.get("event_phase", ""),
            row.get("match_slug", ""),
            row.get("match_url", ""),
            row.get("event", ""),
        ),
        axis=1,
    )

    return existing_df.to_dict("records")


def build_pistol_dataset(match_df: pd.DataFrame, resume: bool = False) -> pd.DataFrame:
    print("=" * 80)
    print("STEP 2: BUILDING MAP COMP + PISTOL DATASET")
    print("=" * 80)

    if "match_url" not in match_df.columns:
        raise ValueError(f"{MATCH_LINKS_CSV} must contain a column named 'match_url'.")

    roster_cache = load_roster_cache()

    if resume:
        processed_match_ids = get_processed_match_ids_from_existing_output()
        all_rows = load_existing_output_rows()

        print(f"Resume mode enabled.")
        print(f"Existing output rows loaded: {len(all_rows)}")
        print(f"Already processed match IDs: {len(processed_match_ids)}")
        print()
    else:
        processed_match_ids = set()
        all_rows = []

    total_matches = len(match_df)

    print(f"Loaded {total_matches} match links.")
    print()

    for index, row in match_df.iterrows():
        match_url = str(row["match_url"]).strip()
        match_id = str(row.get("match_id", ""))

        if not match_id or match_id == "nan":
            match_id = extract_match_id_from_url(match_url)

        match_slug = str(row.get("match_slug", ""))

        if not match_slug or match_slug == "nan":
            match_slug = extract_match_slug_from_url(match_url)

        event_phase = infer_event_phase(
            row.get("event_phase", ""),
            match_slug,
            match_url,
            row.get("event", ""),
        )

        if resume and match_id in processed_match_ids:
            print(f"[resume] Skipping already processed match ID: {match_id}")
            continue

        print("=" * 80)
        print(f"Match {index + 1}/{total_matches}")
        print(f"Match ID: {match_id}")
        print(f"Phase: {event_phase}")
        print(f"URL: {match_url}")
        print("=" * 80)

        try:
            soup = get_soup(match_url, label="match page")
        except Exception as e:
            print(f"FAILED to read match page: {e}")
            continue

        team_1, team_2 = extract_match_teams(soup, match_url)

        print(f"Teams: {team_1} vs {team_2}")

        map_items = parse_map_nav_items(soup)

        if not map_items:
            print("  No map tabs found.")
            continue

        vod_links_by_map = extract_vod_links_by_map(soup)

        print(f"  Found {len(map_items)} map(s): {[m['map_name'] for m in map_items]}")
        print(f"  Found {len(vod_links_by_map)} VOD link(s): {vod_links_by_map}")

        match_rows = []

        for map_item in map_items:
            map_rows, roster_cache = extract_map_rows_from_match(
                soup=soup,
                match_url=match_url,
                match_id=match_id,
                match_slug=match_slug,
                team_1=team_1,
                team_2=team_2,
                map_item=map_item,
                vod_links_by_map=vod_links_by_map,
                roster_cache=roster_cache,
            )

            for map_row in map_rows:
                map_row["event_phase"] = event_phase

            match_rows.extend(map_rows)

        all_rows.extend(match_rows)

        output_df = pd.DataFrame(all_rows)
        output_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
        save_roster_cache(roster_cache)

        print(f"  Match complete. Progress saved to {OUTPUT_CSV}")
        print(f"  Roster cache saved to {ROSTER_CACHE_JSON}")
        print()

    output_df = pd.DataFrame(all_rows)

    output_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    save_roster_cache(roster_cache)

    print("=" * 80)
    print("DONE")
    print("=" * 80)
    print(f"Saved {len(output_df)} rows to: {OUTPUT_CSV}")
    print(f"Roster cache saved to: {ROSTER_CACHE_JSON}")

    return output_df


def print_quick_summary(df: pd.DataFrame):
    if df.empty:
        print("No data collected.")
        return

    print()
    print("=" * 80)
    print("QUICK SUMMARY")
    print("=" * 80)

    print(f"Rows: {len(df)}")

    if "match_id" in df.columns:
        print(f"Unique matches: {df['match_id'].nunique()}")

    if len(df) >= 2:
        print(f"Approx unique map instances: {len(df) // 2}")

    if "event_phase" in df.columns:
        print()
        print("Rows by event phase:")
        print(df["event_phase"].value_counts(dropna=False).sort_index())

    if "team" in df.columns:
        print()
        print("Rows by team:")
        print(df["team"].value_counts().sort_index())

    if "map_played" in df.columns:
        print()
        print("Rows by map:")
        print(df["map_played"].value_counts().sort_index())

    if "pistol_parse_status" in df.columns:
        print()
        print("Pistol parse status:")
        print(df["pistol_parse_status"].value_counts(dropna=False))

    if "overtime_detected" in df.columns:
        print()
        print("Overtime detected:")
        print(df["overtime_detected"].value_counts(dropna=False))

    if "vod_url" in df.columns:
        print()
        print(f"Rows with VOD URL: {df['vod_url'].astype(str).str.len().gt(0).sum()}")

    if "players" in df.columns:
        print()
        print(f"Rows with player names: {df['players'].astype(str).str.len().gt(0).sum()}")


# ==============================
# CLI / MAIN
# ==============================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Build VCT Pacific Stage 1 pistol dataset from VLR."
    )

    parser.add_argument(
        "--event-url",
        default="",
        help="Optional single VLR event URL. If used, this overrides the default Group Stage + Playoffs collection.",
    )

    parser.add_argument(
        "--split",
        default="both",
        choices=["both", "group-stage", "playoffs"],
        help="Which Pacific Stage 1 phase to collect when --event-url is not used. Default: both.",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing vlr_stage1_pistol_dataset.csv and skip already processed match IDs.",
    )

    parser.add_argument(
        "--no-limiter",
        action="store_true",
        help="Disable request limiter. Not recommended.",
    )

    return parser.parse_args()


def main():
    global USE_REQUEST_LIMITER

    args = parse_args()

    if args.no_limiter:
        USE_REQUEST_LIMITER = False

    print("RRQ / VCT Pacific Pistol Analysis Builder")
    print()

    if args.event_url:
        event_targets = [
            {
                "event_phase": "Custom",
                "url": args.event_url.strip(),
            }
        ]
    else:
        if args.split == "group-stage":
            event_targets = [DEFAULT_EVENT_URLS[0]]
        elif args.split == "playoffs":
            event_targets = [DEFAULT_EVENT_URLS[1]]
        else:
            event_targets = DEFAULT_EVENT_URLS

    print("Event target(s):")
    for target in event_targets:
        print(f"- {target.get('event_phase', '')}: {target.get('url', '')}")
    print()

    print(f"Request limiter: {USE_REQUEST_LIMITER}")
    print(f"Resume mode: {args.resume}")
    print()

    match_df = build_match_links_csv(event_targets)

    if match_df.empty:
        print("No match links found. Stopping.")
        return

    output_df = build_pistol_dataset(match_df, resume=args.resume)

    print_quick_summary(output_df)

    print()
    print(f"Final dataset saved to: {OUTPUT_CSV}")
    print(f"Roster cache saved to: {ROSTER_CACHE_JSON}")


if __name__ == "__main__":
    main()
