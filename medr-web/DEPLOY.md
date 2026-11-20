# Putting Medr online

This folder is the web version of Medr. People open a link, create an account and use it. There's nothing to install.

Everything is already built: accounts, private projects per person, daily usage caps, the patient-data notice, the feedback button and your admin page. You only need to create two accounts and paste a few settings. It takes about 20 minutes.

**What it costs:** Render's Starter plan plus a 1 GB disk, about $7–8 a month (check render.com/pricing). On top of that you pay your Claude API usage. Set a spending limit in the Anthropic Console (Settings → Limits) before you share the link.

---

## 1. Put the code on GitHub (5 min)

1. Make a free account at **github.com**.
2. Click **+** (top right) → **New repository**. Name it `medr`, choose **Private**, then click **Create repository**.
3. On the next page, click **uploading an existing file**.
4. Unzip `medr-web.zip`, open the `medr-web` folder, select everything inside it, and drag it all into the browser. Include the hidden `.dockerignore` if you can see it; on a Mac, press Cmd + Shift + . to show hidden files.
5. Click **Commit changes**.

## 2. Deploy on Render (10 min)

1. Make an account at **render.com** and sign in with GitHub.
2. Click **New +** → **Blueprint**, then pick your `medr` repo. Render reads `render.yaml` and sets everything up.
3. It asks for these settings:

| Setting | What to put |
|---|---|
| `ANTHROPIC_API_KEY` | Your Claude API key. It stays on the server, and users never see it. |
| `MEDR_ADMIN_EMAIL` | The email you'll sign up with. It gets the Admin page. |
| `MEDR_INVITE_CODE` | Optional. If set, people need this code to sign up, e.g. `medr-test`. Good while testing. Clear it later to open sign-ups. |
| `NCBI_API_KEY` | Strongly recommended. Discover checks about 100 links in PubMed per map, and this key makes that about 3x faster (roughly 15 seconds instead of 45). It's free at account.ncbi.nlm.nih.gov/settings → "API Key Management". |

4. Click **Apply**. The first build takes about 3–5 minutes.
5. You get a link like `https://medr-xxxx.onrender.com`. **Open it and sign up with your admin email right away**, so nobody else can claim it.

## 3. Check it works (2 min)

- Sign up, create a project and run one search.
- Click **Send feedback** in the sidebar and send a test message.
- Open **Admin** (sidebar or top bar). You should see yourself and the feedback.

You're live. Share the link (and the invite code, if you set one).

---

## Day to day

- **Admin page** (`/#/admin`): users vs. your 200 goal, weekly sign-ups and active users, most-used features, everyone's feedback, and the people list.
- **Forgot password:** on the Admin page, click **Reset password** next to the person. Send them the temporary password, and they can change it in Settings.
- **Problem user:** **Turn off** blocks their account.
- **Daily caps:** each person gets 12 deep runs (Idea Lab, Assessment Finder, plans) and 150 searches a day. Across everyone there are 150 deep runs a day. Caps reset at 7pm Eastern. Change them in Render → your service → **Environment**:
  - `MEDR_DAILY_RUNS`, `MEDR_DAILY_ACTIONS`, `MEDR_GLOBAL_DAILY_RUNS`
  - Your own account has no limits.
- **Updating Medr:** upload the changed files to the GitHub repo again (same "Add file → Upload files" button). Render redeploys by itself in a few minutes. Accounts and projects are kept on the disk.
- **Backups:** Medr copies its database every day into `/data/backups` and keeps 14 days. People can also download any project from Settings.
- **Your own domain (later):** in Render, open Settings → Custom Domains and follow the steps.

## Privacy, in one paragraph

Medr only uses public research (PubMed, ClinicalTrials.gov, preprints) and never asks for patient data. People must tick a box confirming they won't enter any before they can create an account, and the sidebar repeats it. Passwords are stored as scrypt hashes, not as text. Each person's projects are private to their account, and they can delete their account and everything in it from Settings. Medr is not HIPAA-certified, so keep it to public research.
