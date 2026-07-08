# Putting this project on GitHub

This lets you `git clone`/`git pull` the bot onto any device instead of
manually copying files around. Here's the one-time setup, plus how updates
work going forward.

**What I can do vs. what only you can do:** I can set up the local git
repository, `.gitignore`, and commits inside your project folder - those
are just file operations. I can NOT run `git push` myself, because that
needs YOUR GitHub login/token, which I don't have and shouldn't ask for.
So: I prep everything locally, you create the empty repo on GitHub and run
the push yourself (one command, shown below).

**Security note:** your Telegram bot token used to be hardcoded in
`config.py`. As of this update, it isn't anymore - both that and your Angel
One credentials are environment-variable-only now (see `.env.example`),
specifically so nothing sensitive ends up in git history. Set
`TB_TELEGRAM_TOKEN` / `TB_TELEGRAM_CHAT_ID` as environment variables on this
Mac the same way you already did for the Angel One ones, or Telegram alerts
will stop working after this change until you do.

---

## 1. Set the two new environment variables (do this first)

**Mac Terminal**, add to `~/.zshrc` (or wherever you added the Angel One ones):
```
export TB_TELEGRAM_TOKEN="your_bot_token_from_botfather"
export TB_TELEGRAM_CHAT_ID="your_chat_id"
```
Then `source ~/.zshrc` and restart the bot (`./stop_bot.sh` / `./start_bot.sh`)
to confirm Telegram alerts still work.

## 2. Create the GitHub repo (you do this)

1. Go to [github.com/new](https://github.com/new).
2. Repository name: whatever you like (e.g. `nse-options-bot`).
3. Visibility: **Private** (recommended - even with the token removed from
   the code, there's no reason to make your strategy logic public unless
   you want to).
4. **Do NOT** check "Add a README" or any other initialization option -
   leave it completely empty. (We already have all the files locally;
   an auto-created README would conflict with the push.)
5. Click **Create repository**. GitHub will show you a URL like
   `https://github.com/YOUR_USERNAME/YOUR_REPO.git` - copy it.

## 3. Push it up (you run this, in Terminal, in the Trading Bot folder)

```
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git branch -M main
git push -u origin main
```

The first push will likely prompt you to log into GitHub (a browser window
for GitHub's own sign-in, or a username/personal access token prompt if
you're on an older git version - GitHub's own docs cover this if asked:
[docs.github.com - authentication](https://docs.github.com/en/authentication)).
That login is between you and GitHub directly - I never see it.

---

## Going forward: how updates work

- When I make a code change, I'll run `git add` / `git commit` locally
  right here in your project folder - so the change is captured in git
  history immediately, with a clear message describing what changed.
- **You run `git push`** whenever you want to sync those commits up to
  GitHub (I'll remind you after making changes, but you can also just run
  it any time: `git push`).
- On another device, `git pull` (after the initial `git clone`) brings it
  up to date with whatever's been pushed.
- If you ever edit files directly on a device yourself (not through me),
  `git add -A && git commit -m "your message" && git push` from that
  device works the same way.

## What's NOT tracked in git (see `.gitignore`)

Deliberately excluded so the repo stays small and no device overwrites
another's local data on `git pull`:
- `angelone_instruments_cache.json` (36MB, auto-downloads on first run)
- `monitor.log`, `bot.pid`, `logs/` (per-device runtime files)
- `paper_trades.csv` and its `.bak` backups (your personal trading data -
  back this up yourself if you want to preserve it, e.g. copy the file
  somewhere safe periodically)
- `backtest_report.html` (regenerate any time with `python3 backtest_report.py`)
