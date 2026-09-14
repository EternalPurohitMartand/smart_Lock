# Google Cloud OAuth Setup Guide

Follow these steps **once** to get your Google Client ID and Secret for login.

---

## Step 1: Create a Google Cloud Project

1. Go to https://console.cloud.google.com/
2. Sign in with your Google account
3. Click the project dropdown (top-left, next to "Google Cloud")
4. Click **New Project**
5. Name it `Smart Lock Dashboard` (or anything you like)
6. Click **Create**
7. Wait ~30 seconds, then select the new project from the dropdown

---

## Step 2: Configure the OAuth Consent Screen

1. In the left sidebar, go to **APIs & Services → OAuth consent screen**
2. Select **External** user type (works for any Google account)
3. Click **Create**
4. Fill in:
   - **App name**: `Smart Lock Dashboard`
   - **User support email**: your email
   - **Developer contact email**: your email
5. Click **Save and Continue**
6. **Scopes** screen: click **Add or Remove Scopes**, select:
   - `openid` (required)
   - `.../auth/userinfo.email` (required)
   - `.../auth/userinfo.profile` (required)
7. Click **Save and Continue**
8. **Test users** screen: add your own Google email (and any other testers)
9. Click **Save and Continue**

---

## Step 3: Create OAuth 2.0 Credentials

1. In the left sidebar, go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. Application type: **Web application**
4. Name: `Smart Lock Web Client`
5. **Authorized redirect URIs** — add these exactly:
   ```
   http://127.0.0.1:8000/api/auth/google/callback
   ```
   (If you plan to deploy, add your production URL too)
6. Click **Create**
7. Copy the **Client ID** and **Client Secret**

---

## Step 4: Set Environment Variables

Create a `.env` file in the project root (or set env vars):

```
GOOGLE_CLIENT_ID=your-client-id-here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret-here
```

**Never commit `.env` to git.** It contains secrets.

---

## Step 5: Run the Server

```bash
python server.py
```

Open http://127.0.0.1:8000 — you'll see a Google login button.

---

## Multi-Tenant Model

- **First Google account** to log in and register a lock becomes **admin** for that lock
- **Multiple admins** can each register their own lock(s)
- **Admin dashboard** shows: location map, event logs, risk scores for YOUR lock only
- **Super admin** (you, the website owner) sees: all users, all lock IDs, platform analytics

---

## Testing Locally

- OAuth redirect URI must be `http://127.0.0.1:8000/api/auth/google/callback`
- localhost works fine for development
- Google blocks non-HTTPS in production unless you add your domain in consent screen

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "redirect_uri_mismatch" | Check the URI matches exactly (no trailing slash, correct port) |
| "access_not_configured" | Enable the Google+ API or People API in APIs & Services |
| "invalid_client" | Client ID or Secret is wrong — re-copy from Credentials page |
| "token expired" | Normal — server refreshes automatically |
