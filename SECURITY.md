# Security Policy

## Supported Versions

Only the latest release is actively maintained and receives security fixes.
There is no long-term-support branch — fixes ship in the next tagged release.

| Version | Supported |
|---------|-----------|
| Latest  | ✅ |
| Older   | ❌ |

Releases are checksummed and GPG-signed; see [VERIFICATION.md](VERIFICATION.md)
for how to verify a download.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Use GitHub's private **[Report a vulnerability](https://github.com/0xra0/bethesda-strings-editor/security/advisories/new)** form instead.

Include:
- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected version(s)

You can expect an acknowledgement within **72 hours** and a fix or mitigation
plan within **14 days** for confirmed issues.

## Scope

Areas most relevant to security review, with where they live:

**Secrets**
- **Claude API key** — AES-256-GCM in an encrypted file, or the system keyring
  when `keyring` is installed; key derived from the machine ID via
  PBKDF2-HMAC-SHA256 (`gui/secret_store.py`). Never written to the JSON config.
- **NexusMods API key and SSO token** — XOR+base64 obfuscation in the JSON
  config (`gui/app_settings.py`, `_obfuscate`/`_deobfuscate`). This is
  obfuscation, not encryption, and is documented as such — it stops casual
  shoulder-reading of a config file, nothing more.
- **MCP server authorization tokens** — same obfuscation, applied per entry
  (`_obfuscate_mcp_entry`), because the servers list is nested JSON.

**Network and third parties**
- **NexusMods free-user download** — reads session cookies out of
  Firefox/Chromium SQLite databases on the local machine
  (`gui/nexusmods_client.py`, `_read_sqlite_cookies` / `find_browser_cookies`).
- **NexusMods SSO** — a hand-rolled RFC-6455 WebSocket client over stdlib
  `socket`/`ssl` to `wss://sso.nexusmods.com` (`gui/nexusmods_sso.py`); frame
  encode/decode is the parsing surface.
- **Claude MCP connector** — when MCP servers are configured, chat content is
  sent to Anthropic, which connects to those servers and runs their tools
  server-side (`gui/claude_client.py`). Chat only: the translation pipeline
  never issues tool calls.
- **Claude Code CLI backend** — spawns the local `claude` binary as a
  subprocess (`gui/claude_code_client.py`). It strips `ANTHROPIC_API_KEY` and
  `ANTHROPIC_AUTH_TOKEN` from the child environment so the CLI cannot silently
  bill an API key, and runs in an empty scratch directory so no project
  `CLAUDE.md` is auto-discovered.
- **Update check** — polls the GitHub releases API and downloads the chosen
  asset over HTTPS to `~/Downloads`, then opens the folder (`gui/updater.py`,
  `gui/update_dialog.py`). It never extracts or executes the download, and it
  does **not** verify the checksum — that is left to the user.

**Parsing untrusted files** (all of it runs on files downloaded from mod sites)
- Binary `.strings`/`.dlstrings`/`.ilstrings` (`bethesda_strings/core.py`)
- BA2 archives (`bethesda_strings/ba2_handler.py`)
- ESP/ESM/ESL plugins, including the VMAD script-property parser
  (`bethesda_strings/esp_handler.py`, `vmad_handler.py`)
- Scaleform SWF fonts and UI widgets (`bethesda_strings/swf.py`,
  `font_checker.py`, `swf_widgets.py`)
- ZIP / 7z / RAR extraction of downloaded mod archives
  (`gui/nexusmods_browser_dialog.py`, `_extract_from_archive`) — including the
  CLI fallbacks (`7z`, `unrar`) used when `py7zr`/`rarfile` are absent

**Writing outside the project**
- **Plugin writers** — `EspFile.save()` writes a translated *copy* to a path the
  user chooses, while `apply_vmad_translations()` rewrites the plugin in place
  (writing a `.bak` first). Both recompute record and GRUP sizes, so a parsing
  mistake becomes a malformed file the game loads.
- **Desktop integration** — `gui/file_associations.py` writes to
  `$XDG_DATA_HOME` on Linux and `HKCU\Software\Classes` on Windows, and
  registers a command line that Explorer/the file manager will execute with a
  user-supplied path.
- **Audit log** — append-only JSON-lines log of file operations and translation
  batches; it must **never** record string content (`gui/audit_log.py`).

## Out of Scope

- Vulnerabilities in third-party dependencies (report to the respective
  upstream project)
- Issues requiring physical access to the machine
- Social engineering
- Translation quality, model output, or anything an AI backend returns —
  incorrect or unsafe *content* is a bug report, not a security report
- The obfuscation of the NexusMods key being reversible: it is documented as
  obfuscation, and recovering it already requires read access to the user's
  config file
