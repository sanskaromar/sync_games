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

EPIC_CLIENT_AUTH = "MzRhMDJjZjhmNDQxNGUyOWIxNTkyMTg3NmRhMzZmOWE6Zdk=" # standard public launcher client
EPIC_OAUTH_URL = "https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/token"
EPIC_ASSETS_URL = "https://launcher-public-service-prod06.ol.epicgames.com/launcher/api/public/assets/Windows?label=Live"
EPIC_CATALOG_URL = "https://catalog-public-service-prod06.ol.epicgames.com/catalog/api/shared/namespace/{ns}/bulk/items"

EPIC_HEADERS = {
    "User-Agent": "EpicGamesLauncher/11.0.1.0 Windows/10.0.19041.1.256.64bit"
}


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
# 1. STEAM SYNC
# ---------------------------------------------------------
def auto_detect_steam_id():
    possible_paths = [
        r"C:\Program Files (x86)\Steam\config\loginusers.vdf",
        r"C:\Program Files\Steam\config\loginusers.vdf",
        os.path.expanduser(r"~/Library/Application Support/Steam/config/loginusers.vdf")
    ]
    for path in possible_paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            ids = re.findall(r'"(7656119\d{10})"', content)
            if ids:
                return ids[0]
    return ""


def fetch_steam_games(cfg):
    if not cfg.get("steam_id"):
        detected_id = auto_detect_steam_id()
        if detected_id:
            cfg["steam_id"] = detected_id
            save_config(cfg)
        else:
            cfg["steam_id"] = input("[Steam] Enter your 17-digit SteamID (press Enter to skip): ").strip()
            save_config(cfg)

    if not cfg.get("steam_api_key"):
        print("\n[Steam] Opening Steam Web API Key page...")
        webbrowser.open("https://steamcommunity.com/dev/apikey")
        key = input("Paste your Steam Web API Key here (press Enter to skip): ").strip()
        if not key:
            return None
        cfg["steam_api_key"] = key
        save_config(cfg)

    if not cfg.get("steam_api_key") or not cfg.get("steam_id"):
        return None

    print("[Steam] Syncing owned games...")
    try:
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
    except Exception as e:
        print(f"[Steam Error] Failed to sync Steam: {e}")
        return None


# ---------------------------------------------------------
# 2. EPIC GAMES SYNC (Session-based with Error Catching)
# ---------------------------------------------------------
def fetch_epic_games(cfg):
    session = requests.Session()
    session.headers.update(EPIC_HEADERS)

    # Clean client auth (fixing any placeholder truncation)
    client_auth = "MzRhMDJjZjhmNDQxNGUyOWIxNTkyMTg3NmRhMzZmOWE6ZGFhZmJjY2M3Mzc3NDUwMzlkZmZlNTNkOTRmYzc2Y2Y="
    token_headers = {
        "Authorization": f"Basic {client_auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    try:
        access_token = None
        if cfg.get("epic_refresh_token"):
            print("[Epic] Refreshing saved session...")
            resp = session.post(EPIC_OAUTH_URL, headers=token_headers, data={
                "grant_type": "refresh_token",
                "refresh_token": cfg["epic_refresh_token"],
                "token_type": "eg1"
            }, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                cfg["epic_refresh_token"] = data["refresh_token"]
                save_config(cfg)
                access_token = data["access_token"]
            else:
                print(f"[Epic] Refresh token expired (Code {resp.status_code}). Re-authenticating...")

        if not access_token:
            auth_url = (
                "https://www.epicgames.com/id/login?redirectUrl="
                "https%3A%2F%2Fwww.epicgames.com%2Fid%2Fapi%2Fredirect%3F"
                "clientId%3D34a02cf8f4414e29b15921876da36f9a%26responseType%3Dcode"
            )
            print("\n[Epic] Opening browser for authentication...")
            webbrowser.open(auth_url)
            code = input("Paste the 'authorizationCode' value from your browser here: ").strip().strip('"')
            if not code:
                return None

            resp = session.post(EPIC_OAUTH_URL, headers=token_headers, data={
                "grant_type": "authorization_code",
                "code": code,
                "token_type": "eg1"
            }, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            cfg["epic_refresh_token"] = data["refresh_token"]
            save_config(cfg)
            access_token = data["access_token"]

        print("[Epic] Syncing owned games asset list...")
        session.headers.update({"Authorization": f"Bearer {access_token}"})
        
        resp = session.get(EPIC_ASSETS_URL, timeout=20)
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
            r = session.get(url, params=params, timeout=15)
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

    except Exception as e:
        print(f"[Epic Error] Encountered an issue during Epic sync: {e}")
        return None


# ---------------------------------------------------------
# 3. UBISOFT SYNC
# ---------------------------------------------------------
def fetch_ubisoft_games(cfg):
    config_path = cfg.get("ubisoft_config_path", "")
    if not os.path.exists(config_path):
        return None

    print("[Ubisoft] Reading local cache...")
    try:
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
    except Exception as e:
        print(f"[Ubisoft Error]: {e}")
        return None


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def load_existing_catalog():
    if os.path.exists(JSON_OUTPUT):
        with open(JSON_OUTPUT, "r", encoding="utf-8") as f:
            return json.load(f).get("games", [])
    return []


def main():
    parser = argparse.ArgumentParser(description="Sync game libraries locally.")
    parser.add_argument("--steam", action="store_true", help="Sync Steam")
    parser.add_argument("--epic", action="store_true", help="Sync Epic Games")
    parser.add_argument("--ubi", action="store_true", help="Sync Ubisoft")
    parser.add_argument("--all", action="store_true", help="Sync all stores")
    args = parser.parse_args()

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

    print(f"\nSuccess! Catalog updated with {len(final_games)} total games.")


if __name__ == "__main__":
    main()