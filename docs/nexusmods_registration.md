# Nexus Mods API — Application Registration Package

This file is the ready-to-send package for registering **Bethesda Strings
Editor** as a public-facing Nexus Mods API application, per the
[API Acceptable Use Policy](https://help.nexusmods.com/article/114-api-acceptable-use-policy).

Registration is a **manual, one-time** process handled by Nexus Mods staff —
there is no self-serve form. The three steps they require:

1. Email **support@nexusmods.com** with a testing build that works with a
   personal API key.
2. Send a **name**, **short description**, and **logo** for the API Access page.
3. They issue a **slug** for use with SSO.

Once you have the slug, enter it in the app: **Settings → NexusMods → "SSO App
Slug"** (or set the `NEXUSMODS_SSO_SLUG` env var). See
[`gui/nexusmods_sso.py`](../gui/nexusmods_sso.py).

---

## 0. Current status — registered & approved ✅

**Resolved.** The application is **registered with Nexus Mods** and the mod page
is **public again (un-quarantined)**. Nexus Mods staff issued the SSO
application slug **`0xra-bethesdastringseditor`**, which now ships as the
built-in `_DEFAULT_SLUG` in [`gui/nexusmods_sso.py`](../gui/nexusmods_sso.py).

The app no longer stores or uses **personal** API keys anywhere — the only
key-acquisition path is the browser-based **SSO** flow (Settings → NexusMods →
"Sign in with Nexus Mods"), which issues a per-user, app-scoped key held only on
the user's machine. That clears the [API Acceptable Use Policy](https://help.nexusmods.com/article/114-api-acceptable-use-policy)
violation (a public-facing app must not use personal API keys) the page was
originally quarantined for.

> The sections below are retained as a **historical record** of the registration
> package that was sent to Nexus Mods staff; nothing in them is an outstanding
> action.

---

## 1. Application name

> **Bethesda Strings Editor**

(Matches the GitHub repo and the Windows `ProductName` in
`bethesda_strings_editor.spec`.)

## 2. Short description (for the API Access page)

> A free, open-source (MIT) desktop editor for translating and localising
> Bethesda game files — Starfield, Skyrim and Fallout 4
> `.strings`/`.dlstrings`/`.ilstrings`, ESP/ESM/ESL plugins and BA2 archives.
> It integrates the Nexus Mods API so translators can search, preview and
> download translation mods — and optionally publish their own translations —
> without leaving the app. Every API call is user-initiated; the user's key is
> stored only on their own machine and is never sent to any third-party server.

## 3. Logo

- **File:** [`resources/app_icon.png`](../resources/app_icon.png)
- **Format / size:** PNG, **512 × 512** (high-res; downscales cleanly).
- **Dark-background check:** the emblem is a light silver/cyan compass star over
  a dark navy starfield, so it stays clearly visible on a dark background ✔.
- Optional banner (if they want a wide image):
  [`resources/nexusmods_header.png`](../resources/nexusmods_header.png) (1300 × 300).

---

## 4. Intended API usage (compliance + rate-limit summary)

Include this so support can confirm the app complies and won't cause excessive
request volume:

- **All requests are strictly user-initiated.** Nothing polls, scrapes, or runs
  on a timer. One request maps to one explicit user action (run a search, open a
  mod, click download, click upload).
- **Endpoints used:**
  - `POST api.nexusmods.com/v2/graphql` — mod **search** (`nameStemmed:
    MATCHES`) when the user runs a search. Falls back to `search.nexusmods.com`
    only if GraphQL is unreachable.
  - `GET /v1/games/{domain}/mods/{mod_id}.json` — mod metadata when the user
    opens a result.
  - `GET /v1/games/{domain}/mods/{mod_id}/files.json` — file list for a mod the
    user opened (filters out OLD_VERSION / ARCHIVED entries).
  - `GET /v1/games/{domain}/mods/{mod_id}/files/{file_id}/download_link.json` —
    only when the user clicks "Download".
  - **v3 multipart upload** — only when the user uploads *their own* translation
    release.
  - `wss://sso.nexusmods.com` — authentication (after registration).
- **Rate-limit friendliness:** mod thumbnails are cached to local disk; search
  results are never auto-refreshed; a translation cache + translation memory
  avoid redundant work; results are capped (search returns ≤30).
- **Key handling (policy-compliant):** the API key is stored **only on the
  user's machine** — system keyring or an AES-256-GCM encrypted file — never on
  a server, never embedded in the distributed binary. The SSO connection token
  is likewise stored locally and obfuscated. Users can revoke the key at any
  time from their API Access page.

## 5. Testing build (so they can verify with a personal API key)

> **Historical.** This described the pre-registration testing path. SSO is now
> registered, so the current app has **no** personal-API-key field — sign-in is
> SSO-only. The steps below are how staff verified the build during review.

To exercise the integration (as it stood during review):

1. Get the build:
   - **Prebuilt:** GitHub Releases —
     https://github.com/0xra0/bethesda-strings-editor/releases
   - **From source:** `pip install -r requirements.txt && python main.py`
2. Open **Settings → NexusMods**, paste a personal API key
   (https://www.nexusmods.com/users/myaccount?tab=api).
3. Open the **NexusMods Browser** (Translation menu) and:
   - run a search (GraphQL search),
   - open a result (mod info + file list),
   - download a file (download_link).
4. (Optional) demonstrate upload from the NexusMods upload dialog.

---

## 6. Email to send

**To:** support@nexusmods.com
**Subject:** API application registration — Bethesda Strings Editor (public-facing)

```
Hi Nexus Mods team,

Following your reply on my quarantined mod and the API Acceptable Use Policy,
I'd like to register my application for public use and SSO.

Application name: Bethesda Strings Editor
Repository:       https://github.com/0xra0/bethesda-strings-editor
License:          MIT (open source)
Testing build:    https://github.com/0xra0/bethesda-strings-editor/releases
                  (or build from source: pip install -r requirements.txt && python main.py)

What it is:
A free, open-source desktop editor for translating and localising Bethesda game
files (Starfield, Skyrim, Fallout 4) — .strings/.dlstrings/.ilstrings, ESP/ESM/
ESL plugins and BA2 archives. It integrates your API so translators can search,
preview and download translation mods, and optionally publish their own
translations, without leaving the app.

How it uses the API (all calls are user-initiated; nothing polls or scrapes):
- GraphQL v2 search (nameStemmed: MATCHES) when the user runs a search
- REST v1 mods/{id}.json, files.json and download_link.json when the user opens
  a mod and chooses a file to download
- v3 multipart upload only when the user uploads their own translation release
- SSO (wss://sso.nexusmods.com) for authentication once registered

Rate-limit care: thumbnails are cached to disk, results are never auto-
refreshed, results are capped at 30, and a local translation cache/memory
avoids redundant requests. One request = one explicit user action.

Key handling: the API key is stored only on the user's machine (system keyring
or AES-256-GCM encrypted file), never on a server and never embedded in the
distributed binary. I'm migrating users to SSO so they no longer paste a
personal key. The app already implements the SSO flow and just needs the
application slug.

To test with a personal API key: Settings → NexusMods → paste key, then open the
NexusMods Browser to search / open a mod / download a file.

Name, short description and logo for the API Access page:
- Name: Bethesda Strings Editor
- Short description: A free, open-source (MIT) desktop editor for translating and
  localising Bethesda game files (Starfield, Skyrim, Fallout 4) — strings files,
  ESP/ESM/ESL plugins and BA2 archives — with built-in search, preview and
  download of translation mods, and optional upload of the user's own
  translations. All API calls are user-initiated and the key never leaves the
  user's machine.
- Logo: attached (512×512 PNG, visible on a dark background).

Please let me know if you need any changes before issuing a slug. Thank you!

— 0xra
```

> **Attach:** `resources/app_icon.png` (and optionally
> `resources/nexusmods_header.png`).
> **Fill in:** your Nexus Mods username / the account email you want the
> application tied to, if different from the sending address.

---

## 7. After they issue the slug

> ✅ **Done.** The slug `0xra-bethesdastringseditor` was issued and is set as the
> `_DEFAULT_SLUG` in `gui/nexusmods_sso.py`; sign-in works and the mod is
> un-quarantined. The original steps are kept for reference:

1. Open **Settings → NexusMods → "SSO App Slug"** and paste the issued slug
   (or set the `NEXUSMODS_SSO_SLUG` environment variable, or change the
   `_DEFAULT_SLUG` default in `gui/nexusmods_sso.py`).
2. Click **"Sign in with Nexus Mods"** — the browser SSO page should now accept
   the application and return a key (no more "Application ID was invalid").
3. Reply to the support thread to get the mod un-quarantined / re-reviewed.
