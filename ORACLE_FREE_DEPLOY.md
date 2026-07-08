# Running the bot 24/7 for free — Oracle Cloud "Always Free" tier

Oracle Cloud gives you a real, full-power Linux server for free, indefinitely
(not just a 3-month trial) as long as you stay within their "Always Free"
resource limits — which are generous enough for this bot (it barely uses
any CPU/RAM). This is the cheapest way to test the bot 24/7 before deciding
whether to pay for anything.

**Credentials note:** same rule as before — you enter your Angel One and
Telegram credentials directly on the server yourself, never in chat with me.

**A card is required at signup** for identity verification only. Oracle
states Always Free resources are never billed unless you manually upgrade
to a paid account. You do this signup yourself — I won't ask for or handle
your card details.

---

## 1. Sign up

1. Go to **oracle.com/cloud/free/** and click **Start for free**.
2. Fill in your country, name, email — verify the email when prompted.
3. Set a password, verify your phone number (SMS code), then enter card
   details for identity verification.
4. **Home Region**: pick one geographically close to India if offered
   (e.g. a Mumbai/Hyderabad or Singapore region) — you can't change this
   later. If the free "Ampere" capacity is unavailable in that region later
   on, you can still fall back to the smaller always-free AMD shape (see
   below), or try a different region at signup.
5. Wait for account activation — usually fast, occasionally a short delay
   while Oracle verifies the account.

## 2. Create the VM (Compute Instance)

1. In the Oracle Cloud Console, go to **Compute > Instances > Create Instance**.
2. Name it something like `trading-bot`.
3. Under **Image and shape**, click **Edit**:
   - Image: **Canonical Ubuntu 22.04**
   - Shape: click **Change shape**, choose the **Ampere (ARM)** family,
     shape `VM.Standard.A1.Flex`, and set it to something modest like
     **2 OCPUs / 12 GB memory** (well within the free 4 OCPU / 24 GB limit
     — no need to max it out for this bot).
   - Look for the **"Always Free eligible"** tag on the shape - make sure
     it's shown before continuing, so you're not accidentally billed.
   - If Ampere capacity shows unavailable in your region, switch to shape
     `VM.Standard.E2.1.Micro` (AMD, smaller but also Always Free eligible)
     instead - still plenty for this bot.
4. Under **Add SSH keys**, choose **"Generate a key pair for me"** and click
   **Save private key** - this downloads a `.key` file. Keep it safe; you
   need it to log in (there's no simple root password like on Hostinger/
   DigitalOcean).
5. Leave networking on its defaults (it creates a new network and assigns
   you a public IP automatically).
6. Click **Create**. Wait a minute or two, then note the **Public IP
   Address** shown on the instance's details page.

## 3. Connect to it from your Mac

Move the downloaded key somewhere sensible and lock down its permissions
(SSH refuses to use a key that's too open):

```
mv ~/Downloads/ssh-key-*.key ~/.ssh/oracle_trading_bot.key
chmod 400 ~/.ssh/oracle_trading_bot.key
```

Then connect (note the username is `ubuntu`, not `root`, on Oracle's
Ubuntu image):

```
ssh -i ~/.ssh/oracle_trading_bot.key ubuntu@YOUR_VM_PUBLIC_IP
```

Type `yes` if asked to confirm the connection.

## 4. Install prerequisites (on the server)

```
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip
```

## 5. Copy your bot files to the server

From a **new Terminal tab on your Mac**:

```
scp -i ~/.ssh/oracle_trading_bot.key -r "/Users/ajithkhan/Downloads/Claude Projects/Trading Bot" ubuntu@YOUR_VM_PUBLIC_IP:/home/ubuntu/trading-bot
```

## 6. Install Python dependencies (back in the SSH session)

```
cd /home/ubuntu/trading-bot
pip3 install -r requirements.txt
```

## 7. Set your credentials on the server

```
nano .env
```

Paste in your real values:

```
ANGELONE_API_KEY=your_api_key
ANGELONE_CLIENT_CODE=your_client_code
ANGELONE_PIN=your_pin
ANGELONE_TOTP_SECRET=your_totp_secret
```

Save with `Ctrl+O`, `Enter`, exit with `Ctrl+X`.

## 8. Test the connection

```
python3 diagnose_angelone.py
```

Confirm login succeeds from this server before moving on.

## 9. Install it as a service (auto-starts on boot, auto-restarts if it crashes)

Use `tradingbot-oracle.service` (included, already set up for the `ubuntu`
user and `/home/ubuntu/trading-bot` path used above):

```
sudo cp tradingbot-oracle.service /etc/systemd/system/tradingbot.service
sudo systemctl daemon-reload
sudo systemctl enable tradingbot
sudo systemctl start tradingbot
```

Check it's running:

```
sudo systemctl status tradingbot
```

Watch logs live:

```
sudo journalctl -u tradingbot -f
```

## 10. Confirm from Telegram

Send `status` and `summary` to your bot - should reply exactly as before,
now surviving your Mac being off entirely, for free.

---

## Managing it later

- Stop it: `sudo systemctl stop tradingbot`
- Restart after editing files: `sudo systemctl restart tradingbot`
- Update code: `scp` the changed file(s) up again (same `-i` key flag),
  then `sudo systemctl restart tradingbot`
- Check if it's alive: `sudo systemctl status tradingbot`

## Staying within the free tier

- Stick to the "Always Free eligible" shapes/sizes shown at creation time -
  don't resize upward "just to be safe."
- Oracle will email you if you're ever close to a paid resource - if you
  see that, delete/downsize rather than let it convert to billing.
- One VM (even the small Always Free one) is more than enough for this
  bot's workload.
