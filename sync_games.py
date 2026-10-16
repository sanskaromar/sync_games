import os
import re
import csv
import json
import argparse
import webbrowser
import requests
from datetime import datetime

CONFIG_FILE = "catalog_config.json"
CSV_OUTPUT = "game_catalog.csv"
JSON_OUTPUT = "game_catalog.json"

EPIC_CLIENT_AUTH = "MzRhMDJjZjhmNDQxNGUyOWIxNTkyMTg3NmRhMzZmOWE6ZGFhZmJjY2M3Mzc3NDUwMzlkZmZlNTNkOTRmYzc2Y2Y="
EPIC_OAUTH_URL = "https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/token"
EPIC_ASSETS_URL = "https://launcher-public-service-prod06.ol.epicgames.com/launcher/api/public/assets/Windows?label=Live"
EPIC_CATALOG_URL = "https://catalog-public-service-prod06.ol.epicgames.com/catalog/api/shared/namespace/{ns}/bulk/items"


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "steam_api_key": "",
        "steam_id": "",
        "epic_refresh_token": "",
        "ubisoft_config_path": os.path.expandvars(
            r"%LOCALAPPDATA%\Ubisoft Game Launcher\cache\configuration\configurations"
        )
    }


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------
# 1. STEAM (Auto-Detects Local SteamID + Web API)
# ---------------------------------------------------------
def auto_detect_steam_id():
    """Reads your logged-in Steam Launcher's loginusers.vdf to find your 64-bit SteamID."""
    possible_paths = [
        r"C:\Program Files (x86)\Steam\config\loginusers.vdf",
        r"C:\Program Files\Steam\config\loginusers.vdf",
        os.path.expanduser(r"~/Library/Application Support/Steam/config/loginusers.vdf")
    ]
    for path in possible_paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            # Find all 17-digit Steam64 IDs in the file
            ids = re.findall(r'"(7656119\d{10})"', content)
            if ids:
                return ids[0]
    return ""


def fetch_steam_games(cfg):
    # 1. Auto-detect SteamID from installed Steam client if missing
    if not cfg.get("steam_id"):
        detected_id = auto_detect_steam_id()
        if detected_id:
            print(f"[Steam] Auto-detected your logged-in SteamID: {detected_id}")
            cfg["steam_id"] = detected_id
            save_config(cfg)
        else:
            cfg["steam_id"] = input("[Steam] Enter your 17-digit SteamID (or press Enter to skip): ").strip()
            save_config(cfg)

    # 2. Ask once for Steam API key if missing
    if not cfg.get("steam_api_key"):
        print("\n[Steam] One-time setup to read your full cloud library:")
        print("Opening https://steamcommunity.com/dev/apikey in your browser...")
        webbrowser.open("https://steamcommunity.com/dev/apikey")
        key = input("Paste your Steam Web API Key here (or press Enter to skip): ").strip()
        if not key:
            return None
        cfg["steam_api_key"] = key
        save_config(cfg)

    if not cfg.get("steam_api_key") or not cfg.get("steam_id"):
        return None

    print("[Steam] Syncing owned games...")
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
    params = {
        "key": cfg["steam_api_key"],
        "steamid": cfg["steam_id"],
        "include_appinfo": 1,
        "include_played_free_games": 1,
        "format": "json"
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    games = resp.json().get("response", {}).get("games", [])

    catalog = [
        {
            "store": "Steam",
            "app_id": str(g.get("appid")),
            "title": g.get("name", "Unknown"),
            "playtime_hours": round(g.get("playtime_forever", 0) / 60.0, 1)
        }
        for g in games
    ]
    print(f"[Steam] Found {len(catalog)} games.")
    return catalog


# ---------------------------------------------------------
# 2. EPIC GAMES (Browser Handshake Once -> Auto-Refresh Forever)
# ---------------------------------------------------------
def get_epic_access_token(cfg):
    headers = {
        "Authorization": f"Basic {EPIC_CLIENT_AUTH}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    if cfg.get("epic_refresh_token"):
        resp = requests.post(EPIC_OAUTH_URL, headers=headers, data={
            "grant_type": "refresh_token",
            "refresh_token": cfg["epic_refresh_token"],
            "token_type": "eg1"
        })
        if resp.status_code == 200:
            data = resp.json()
            cfg["epic_refresh_token"] = data["refresh_token"]
            save_config(cfg)
            return data["access_token"]
        print("[Epic] Saved session expired. Re-authenticating...")

    auth_url = (
        "https://www.epicgames.com/id/login?redirectUrl="
        "https%3A%2F%2Fwww.epicgames.com%2Fid%2Fapi%2Fredirect%3F"
        "clientId%3D34a02cf8f4414e29b15921876da36f9a%26responseType%3Dcode"
    )
    print("\n[Epic] First-time setup: Opening Epic auth page in your browser...")
    webbrowser.open(auth_url)
    print("Copy the 'authorizationCode' value from the browser page that just opened.")
    code = input("Paste authorizationCode here (or press Enter to skip): ").strip().strip('"')
    if not code:
        return None

    resp = requests.post(EPIC_OAUTH_URL, headers=headers, data={
        "grant_type": "authorization_code",
        "code": code,
        "token_type": "eg1"
    })
    resp.raise_for_status()
    data = resp.json()
    cfg["epic_refresh_token"] = data["refresh_token"]
    save_config(cfg)
    return data["access_token"]


def fetch_epic_games(cfg):
    token = get_epic_access_token(cfg)
    if not token:
        return None

    print("[Epic] Syncing owned games...")
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(EPIC_ASSETS_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    assets = resp.json()

    ns_map = {}
    for asset in assets:
        ns = asset.get("namespace")
        item_id = asset.get("catalogItemId")
        app_name = asset.get("appName")
        if ns and item_id and ns != "ue":
            ns_map.setdefault(ns, {})[item_id] = app_name

    catalog = []
    seen_titles = set()
    for ns, items in ns_map.items():
        url = EPIC_CATALOG_URL.format(ns=ns)
        params = [("id", item_id) for item_id in items.keys()]
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            continue

        for item_id, details in r.json().items():
            title = details.get("title", "Unknown")
            categories = [c.get("path", "") for c in details.get("categories", [])]
            if any("games" in c for c in categories) and not any("addons" in c or "dlc" in c for c in categories):
                if title not in seen_titles:
                    seen_titles.add(title)
                    catalog.append({
                        "store": "Epic Games",
                        "app_id": items.get(item_id, item_id),
                        "title": title,
                        "playtime_hours": 0.0
                    })

    print(f"[Epic] Found {len(catalog)} games.")
    return catalog


# ---------------------------------------------------------
# 3. UBISOFT CONNECT (Optional Local Cache Reader)
# ---------------------------------------------------------
def fetch_ubisoft_games(cfg):
    config_path = cfg.get("ubisoft_config_path", "")
    if not os.path.exists(config_path):
        print(f"[Ubisoft] Cache file not found at {config_path}")
        return None

    print("[Ubisoft] Reading local Ubisoft Connect cache...")
    with open(config_path, "rb") as f:
        raw = f.read().decode("utf-8", errors="ignore")

    titles = set()
    for match in re.finditer(r"root:\s+name:\s*\"?([^\r\n\"]+)\"?", raw):
        name = match.group(1).strip()
        if name and not re.match(r"^(l\d+|GAMENAME|NAME|_.*)$", name, re.IGNORECASE):
            titles.add(name)

    catalog = [
        {"store": "Ubisoft", "app_id": f"ubi_{i}", "title": t, "playtime_hours": 0.0}
        for i, t in enumerate(sorted(titles), 1)
    ]
    print(f"[Ubisoft] Found {len(catalog)} games.")
    return catalog


# ---------------------------------------------------------
# MAIN CLI & SMART MERGE
# ---------------------------------------------------------
def load_existing_catalog():
    if os.path.exists(JSON_OUTPUT):
        with open(JSON_OUTPUT, "r", encoding="utf-8") as f:
            return json.load(f).get("games", [])
    return []


def main():
    parser = argparse.ArgumentParser(description="Sync your game libraries locally.")
    parser.add_argument("--steam", action="store_true", help="Sync Steam library")
    parser.add_argument("--epic", action="store_true", help="Sync Epic Games library")
    parser.add_argument("--ubi", action="store_true", help="Sync Ubisoft library")
    parser.add_argument("--all", action="store_true", help="Sync all 3 stores")
    args = parser.parse_args()

    # Default priority if no flags are passed: Steam + Epic only!
    if not (args.steam or args.epic or args.ubi or args.all):
        args.steam = True
        args.epic = True

    if args.all:
        args.steam = args.epic = args.ubi = True

    cfg = load_config()
    existing_games = load_existing_catalog()
    updated_stores = {}

    if args.steam:
        res = fetch_steam_games(cfg)
        if res is not None:
            updated_stores["Steam"] = res

    if args.epic:
        res = fetch_epic_games(cfg)
        if res is not None:
            updated_stores["Epic Games"] = res

    if args.ubi:
        res = fetch_ubisoft_games(cfg)
        if res is not None:
            updated_stores["Ubisoft"] = res

    # Keep games from stores we didn't sync this run, replace stores we did sync
    final_games = [g for g in existing_games if g["store"] not in updated_stores]
    for store_games in updated_stores.values():
        final_games.extend(store_games)

    final_games.sort(key=lambda x: (x["title"].lower(), x["store"]))

    with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "total_games": len(final_games),
            "games": final_games
        }, f, indent=2, ensure_ascii=False)

    with open(CSV_OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["title", "store", "playtime_hours", "app_id"])
        writer.writeheader()
        writer.writerows(final_games)

    print(f"\nSuccess! Catalog now has {len(final_games)} games across all synced stores.")
    print(f"Saved to: {CSV_OUTPUT} and {JSON_OUTPUT}")


if __name__ == "__main__":
    main()