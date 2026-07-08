# Running this bot on another computer

The bot is plain Python - nothing here is tied to your Mac specifically. To
run it on a different machine (Windows via Command Prompt, another Mac, or
Linux), here's the full process.

**Credentials note, same rule as always:** you'll set BOTH your Angel One
credentials AND your Telegram bot token/chat ID as environment variables ON
THE NEW DEVICE yourself - never copy them into a file, and never paste them
to me in chat. Neither lives inside the code anymore (see GITHUB_SETUP.md
for why) - both are required on every device you run this from, including
this one, from now on.

---

## 1. Install Python on the new device

- **Windows:** download from [python.org/downloads](https://python.org/downloads) -
  during install, check the box **"Add python.exe to PATH"** (easy to miss,
  important).
- **Mac:** already covered in the main README if it's another Mac.
- **Linux:** usually preinstalled; if not, `sudo apt install python3 python3-pip`.

Verify it worked - open Command Prompt (Windows) or Terminal and run:
```
python --version
```
(On Mac/Linux this is sometimes `python3 --version` instead.)

## 2. Get the code onto the new device

**If you've set up the GitHub repo (see GITHUB_SETUP.md) - recommended:**
```
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```
This is the easiest way to keep every device in sync - `git pull` afterward
updates it to whatever you last pushed from anywhere else.

**Without git:** copy the whole `Trading Bot` folder over instead - USB
drive, a synced cloud folder (Google Drive/OneDrive/Dropbox), AirDrop, etc.

Either way, you don't need to copy `angelone_instruments_cache.json` (36MB) -
it re-downloads automatically on first run (and if you used git, it's
excluded from the repo entirely via `.gitignore`).

## 3. Open a terminal and go to the folder

- **Windows (Command Prompt):**
  ```
  cd C:\path\to\Trading Bot
  ```
- **Mac/Linux (Terminal):**
  ```
  cd /path/to/Trading\ Bot
  ```

## 4. Install the Python dependencies

- **Windows:** `pip install -r requirements.txt`
- **Mac/Linux:** `pip3 install -r requirements.txt`

## 5. Set your credentials as environment variables

Six values are needed now (see `.env.example` in the repo for the full
list): four for Angel One, two for Telegram.

**Windows (Command Prompt) - permanent, so you don't retype every time:**
```
setx ANGELONE_API_KEY "your_api_key"
setx ANGELONE_CLIENT_CODE "your_client_code"
setx ANGELONE_PIN "your_pin"
setx ANGELONE_TOTP_SECRET "your_totp_secret"
setx TB_TELEGRAM_TOKEN "your_bot_token"
setx TB_TELEGRAM_CHAT_ID "your_chat_id"
```
Close and reopen Command Prompt afterward for `setx` values to take effect
(they don't apply to the window you ran them in).

**Mac/Linux:** add `export ANGELONE_API_KEY=...` / `export TB_TELEGRAM_TOKEN=...`
(etc.) lines to `~/.zshrc` or `~/.bash_profile`, then `source` that file -
same pattern as you already used on this Mac for the Angel One ones.

## 6. Test the connection

```
python diagnose_angelone.py
```
(`python3` instead of `python` on Mac/Linux.) Confirm login succeeds on this
device before relying on it.

## 7. Run the bot

```
python main.py --mode monitor --paper --broker angelone
```

## 8. Keep it running in the background

- **Mac/Linux:** use the included `start_bot.sh` / `stop_bot.sh` - identical
  to how you run them on this Mac.
- **Windows:** the `.sh` scripts won't run directly in Command Prompt.
  Two Windows-native scripts are included instead:
  - `start_bot.bat` - opens the bot in its own minimized window and logs to
    `monitor.log`, so you can close the Command Prompt window you launched
    it from.
  - `stop_bot.bat` - closes that window.

  **Note:** these two `.bat` files were written without being able to test
  them on an actual Windows machine (this whole project was built from a
  Mac/Linux environment) - they use only standard `start`/`taskkill`
  commands that should work on any modern Windows version, but please run
  them once and tell me if anything doesn't behave as expected so I can fix it.

  Simpler alternative if you'd rather not deal with any of that: just run
  step 7's command directly and leave that Command Prompt window open while
  the market's active - closing it stops the bot, same tradeoff as not using
  `start_bot.sh` on Mac.

## 9. Telegram still works the same way

Since Telegram alerts go through the internet (not your computer directly),
`status`/`summary`/`hold`/`close` all work identically regardless of which
device is actually running the bot.
