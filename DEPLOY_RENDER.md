# Deploying to Render.com

This guide takes you from "zip on your laptop" to "live URL on the internet"
in about 15 minutes.

---

## ⚠️ Before you start — important caveats

### 1. Client data confidentiality

You'll be uploading client GST data (GSTINs, invoices, customer details) to
Render's servers in Singapore. Make sure this is acceptable under your firm's
client engagement letters. If not, run the tool locally instead — see
**Local install** in the main `README.md`.

### 2. Free tier has ephemeral storage

Render's free plan **wipes the file system** on every restart or redeploy.
Practical impact:
- **Firm profiles you add will disappear** after restarts (~daily for free
  instances that go idle).
- **Generated JSONs and reports also get wiped.** Always download immediately
  after generating.

To get persistent storage you have two options:
- **Upgrade to Starter plan ($7/month ≈ ₹600/month)** — adds a 1 GB disk.
  Edit `render.yaml` and uncomment the disk block, redeploy.
- **Stay on free tier** — re-add your firms each time. With only 2 firms,
  this takes ~30 seconds.

### 3. Free tier sleeps when idle

After 15 min of no requests, Render puts your service to sleep. The next
visit takes ~30 seconds to wake it up. After that it's instant until idle
again. Not a problem for monthly GST work but worth knowing.

---

## What you'll need

- A **GitHub account** (free): https://github.com
- A **Render account** (free): https://render.com
- The unzipped `gstr1_tool` folder on your computer
- **Git** installed: https://git-scm.com/download/win
- **Python 3.9+** installed (only to generate your password hash):
  https://python.org

---

## Step 1 — Generate your password hash

The tool stores your password as a bcrypt hash, not plaintext. Generate the
hash now (you'll paste it into Render later).

Open Command Prompt **inside the unzipped `gstr1_tool` folder** and run:

```cmd
pip install bcrypt
python scripts\hash_password.py
```

It'll prompt twice for your password (use 10+ characters). Output looks like:

```
$2b$12$abcDEF...verylonghash...XYZ
```

**Copy this hash to a safe place** — Notepad is fine, just don't save the
file. You'll paste it into Render in Step 5.

Also pick a username (e.g., `admin`, or your initials). Keep that handy too.

---

## Step 2 — Push the code to GitHub

Render deploys directly from a GitHub repository. So we need to put the code
there first.

### 2a. Create a new GitHub repo

1. Go to https://github.com/new
2. Name it something like `gstr1-tool` (private recommended — click the
   "Private" radio button)
3. Don't initialize with README/license/gitignore — we already have these
4. Click **Create repository**
5. Leave the page open — you'll need the URL shown (something like
   `https://github.com/yourname/gstr1-tool.git`)

### 2b. Push from your computer

Open Command Prompt **inside the unzipped `gstr1_tool` folder** and run:

```cmd
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/yourname/gstr1-tool.git
git push -u origin main
```

Replace the URL on the `git remote add` line with the URL from your repo.

GitHub may ask you to log in. Use a personal access token if prompted
(Settings → Developer settings → Personal access tokens → Generate new
token, with `repo` scope).

---

## Step 3 — Deploy on Render

1. Sign in at https://dashboard.render.com
2. Click **New +** (top right) → **Blueprint**
3. Click **Connect GitHub** if you haven't, and authorise Render to access
   your `gstr1-tool` repo
4. Pick the `gstr1-tool` repo from the list
5. Render reads `render.yaml` and shows what it'll create. Click **Apply**

---

## Step 4 — Wait for the build

Render now installs Python, dependencies, and starts the app. This takes
about 3–5 minutes the first time. Watch the build log scroll. When you
see:

```
==> Your service is live 🎉
```

…you're almost there. The service URL appears at the top of the page,
something like `https://gstr1-generator-abcd.onrender.com`.

**Don't open it yet** — login won't work until Step 5.

---

## Step 5 — Set username and password

1. In the Render dashboard, click your service → **Environment** (left sidebar)
2. You'll see two `sync: false` variables that need values:

   | Key | Value |
   |---|---|
   | `APP_USERNAME` | The username you chose (e.g., `admin`) |
   | `APP_PASSWORD_HASH` | The full bcrypt hash from Step 1 |

3. Click **Save Changes** at the bottom
4. Render auto-redeploys with the new values (~1 min)

---

## Step 6 — Sign in

Open the service URL. You'll see the login page. Enter your username and
password (the original one, not the hash).

If it works, you're done. Bookmark the URL.

---

## Adding your firms (after each restart on free tier)

If you're on the free tier and your service has been idle long enough to
restart, your firm profiles will be gone. Re-add them:

1. Sign in
2. Go to **Firms → Add firm**
3. Enter name + GSTIN for each firm

This only takes a few seconds. If it gets annoying, upgrade to the Starter
plan and follow "Enabling persistent storage" below.

---

## Enabling persistent storage (paid tier)

If you want your firm profiles to survive restarts:

1. Open `render.yaml` in your editor
2. Comment out `plan: free` and uncomment the `plan: starter` block, the
   `disk:` block, and the `DATA_ROOT` env var (all marked in the file)
3. Commit and push:
   ```cmd
   git add render.yaml
   git commit -m "Enable persistent disk"
   git push
   ```
4. Render auto-deploys. Cost is $7/month.

After this, firm profiles, uploaded files, and generated outputs all
survive across restarts.

---

## Updating the tool

When you want to make changes (bug fixes, new features), the workflow is:

```cmd
# Make changes locally
# Test locally if possible (cd web && python app.py)
git add .
git commit -m "Describe what changed"
git push
```

Render auto-deploys on every push to `main`. Done.

---

## Custom domain (optional)

If you want `gstr1.yourdomain.com` instead of the long Render URL:

1. Service → **Settings → Custom Domain**
2. Add your domain
3. Update DNS at your domain registrar with the CNAME record Render shows
4. Wait ~10 minutes for SSL to provision

---

## Troubleshooting

**"Service is starting" forever** → Check Logs. Most common cause: missing
env var. Make sure `APP_USERNAME` and `APP_PASSWORD_HASH` are set.

**"FLASK_SECRET_KEY environment variable must be set in production"** →
Either Render didn't auto-generate it (re-deploy from Manual Deploy menu),
or you accidentally cleared it. Add it manually under Environment:
```
python -c "import secrets; print(secrets.token_hex(32))"
```

**Login keeps failing** → Re-generate the hash carefully. Common mistakes:
copying with leading/trailing whitespace, copying only part of the hash
(it's ~60 chars), pasting the password instead of the hash.

**"413 Request Entity Too Large"** → File over 64 MB. Either compress it or
split into smaller sheets.

**Free instance won't wake up** → Click your service → **Manual Deploy** →
**Deploy latest commit**. This forces a wake-up.

**Lost your password** → No recovery. Generate a new hash with
`scripts/hash_password.py`, paste it into Render's `APP_PASSWORD_HASH` env
var. Service redeploys, new password works.

**Firm profiles disappeared** → Free tier restarted. Either re-add them
(takes 30 sec) or upgrade to Starter plan for persistence.

---

## Security notes

- **Always download generated outputs immediately.** Even with persistent
  disk, files older than your retention policy should be deleted via the
  Files page.
- **Password hashing uses bcrypt with 12 rounds** — strong against brute
  force. But the URL itself is publicly reachable, so use a strong password.
- **Sessions expire after 8 hours.** You'll need to sign in again the next
  morning.
- **HTTPS is automatic on Render** — every service gets SSL by default.
- **The `.gitignore` excludes** `web/uploads/`, `web/output/`, and
  `web/data/firms.json` — so client data never lands in your repo even if
  you accidentally `git add .` after running locally.
