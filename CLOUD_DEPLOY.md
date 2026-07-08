# Running the bot 24/7 on a cloud server

Your MacBook has to be powered on for anything to keep running on it - that's
just how computers work. To have the bot alive even when your Mac is off,
it needs to live on a separate small server that never turns off. This is a
one-time setup (~20-30 minutes), then it just runs.

**Cost:** you're using your existing Hostinger account, so this is just a
VPS plan add-on - pick the cheapest KVM tier (KVM 1 or KVM 2), this bot is
lightweight and doesn't need much CPU/RAM.

**Credentials note:** you'll enter your Angel One and Telegram credentials
directly on the server yourself (via its own terminal), the same way you did
on your Mac - never paste them to me in chat.

---

## 1. Create the server (Hostinger VPS)

You're already on the right page (hPanel > VPS).

1. Click **Set up VPS**.
2. Pick the cheapest plan - **KVM 1** or **KVM 2** is plenty for this bot
   (it's just a lightweight Python script making occasional API calls, not
   a heavy workload).
3. Choose **Operating System: Ubuntu 22.04 LTS** (skip any pre-bundled
   panel like CyberPanel/CloudPanel - you don't need those for this).
4. Choose a data center location close to India if offered (e.g. Singapore)
   for lower latency to NSE/Angel One.
5. Set your **root password** during setup (or Hostinger will email/show
   you one) - keep it safe, you'll need it to connect.
6. Complete the purchase (you handle payment yourself).
7. Once it's provisioned, hPanel's VPS dashboard will show your server's
   **IP address** (e.g. `82.180.xxx.xxx`) - note it down.

> Alternative: if you'd rather not buy a Hostinger VPS, any Ubuntu VPS
> (DigitalOcean, AWS Lightsail, etc.) works identically from Step 2 onward -
> just create it there instead and use the IP it gives you.

## 2. Connect to it from your Mac

Open Terminal on your Mac:

```
ssh root@YOUR_VPS_IP
```

Type `yes` if asked to confirm the connection, then enter the root
password you set.

(Hostinger's hPanel also has a built-in browser-based terminal on the VPS
overview page if you'd rather not use Mac Terminal - either works the same
way for the steps below.)

## 3. Install prerequisites (run these ON the server)

```
apt update && apt upgrade -y
apt install -y python3 python3-pip
```

## 4. Copy your bot files to the server

Run this from a **new Terminal tab on your Mac** (not the SSH session):

```
scp -r "/Users/ajithkhan/Downloads/Claude Projects/Trading Bot" root@YOUR_VPS_IP:/root/trading-bot
```

This copies everything - config.py, main.py, etc. - to `/root/trading-bot`
on the server.

## 5. Install Python dependencies (back in the SSH session)

```
cd /root/trading-bot
pip3 install -r requirements.txt
```

## 6. Set your credentials on the server

```
nano .env
```

Paste in (replace with your real values - the ones you already have from
your Mac's environment variables):

```
ANGELONE_API_KEY=your_api_key
ANGELONE_CLIENT_CODE=your_client_code
ANGELONE_PIN=your_pin
ANGELONE_TOTP_SECRET=your_totp_secret
```

Save with `Ctrl+O`, `Enter`, then exit with `Ctrl+X`.

## 7. Test the connection

```
python3 diagnose_angelone.py
```

Confirm login succeeds from this server before moving on.

## 8. Install it as a service (auto-starts on boot, auto-restarts if it crashes)

A ready-made service file is included: `tradingbot.service`. Install it:

```
cp tradingbot.service /etc/systemd/system/tradingbot.service
systemctl daemon-reload
systemctl enable tradingbot
systemctl start tradingbot
```

Check it's running:

```
systemctl status tradingbot
```

Watch its logs live:

```
journalctl -u tradingbot -f
```

(Ctrl+C just exits the log view, doesn't stop the bot.)

## 9. Confirm from Telegram

Send `status` and `summary` to your bot - you should get replies exactly
like before, except now it survives your Mac being off, asleep, or closed.

---

## Managing it later

- Stop it: `systemctl stop tradingbot`
- Restart after editing files: `systemctl restart tradingbot`
- Update code: `scp` the changed file(s) up again, then `systemctl restart tradingbot`
- Check if it's alive any time: `systemctl status tradingbot`

## A note on security

This server will hold live trading credentials, so:
- Use a strong root password (or switch to SSH keys later).
- Keep the server updated (`apt update && apt upgrade` occasionally).
- Don't share the server's IP or password with anyone.

## A note on regulation (only relevant once you place REAL orders)

Since 2026, SEBI/NSE algo trading rules require order-placing APIs to run
from a registered static IP. This bot currently only does paper trading -
no real orders are placed - so this doesn't apply yet. If you later connect
real order placement, the server's static IP (which a cloud VM naturally
has) will actually help satisfy that requirement.
