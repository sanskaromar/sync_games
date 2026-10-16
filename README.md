# sync_games

- It includes auto-detection of your logged-in Steam account, interactive first-time prompts so you never have to manually edit a config file, and store toggle flags.


```bash
# Run Default (Steam + Epic only, skips Ubisoft):
python sync_games.py

#Run ONLY Epic Games:
python sync_games.py --epic

#Run ONLY Steam:
python sync_games.py --steam

# Include Ubisoft when you want it:
python sync_games.py --steam --epic --ubi

#And if you only run --epic today, it merges your updated Epic list with your existing saved Steam games so it never deletes your other stores' data!
```



Exact Step-by-Step: What Happens When You Run It

1. Save and run the file: Save the code above as sync_games.py and run python sync_games.py in your terminal. By default, it will only run Steam and Epic Games (ignoring Ubisoft unless you pass --ubi).
2. Steam auto-detects your ID and opens your browser once: The script reads your local C:\Program Files (x86)\Steam\config\loginusers.vdf file to grab your 17-digit SteamID automatically. Then it pops open your browser to Steam's API Key page—copy your key, paste it into the terminal, and hit Enter.
3. Epic pops open your browser for a 1-time handshake: Next, the script opens a browser tab to Epic Games. Because you are already logged in, you'll immediately see a short block of text like {"redirectUrl":"...","authorizationCode":"a1b2c3d4..."}. Copy that 32-character code, paste it into the terminal, and hit Enter.
4. Done—future updates take 2 seconds: The script saves your key and Epic refresh token into catalog_config.json right next to the script. Next month, when you run python sync_games.py (or python sync_games.py --epic), it won't ask you a single question—it will silently refresh and update your game_catalog.csv and game_catalog.json in 2 seconds.

