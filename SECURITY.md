# Security Policy

## Supported Versions

Only the latest release is actively maintained and receives security fixes.

| Version | Supported |
|---------|-----------|
| Latest  | ✅ |
| Older   | ❌ |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Use GitHub's private **[Report a vulnerability](https://github.com/0xra0/bethesda-strings-editor/security/advisories/new)** form instead.

Include:
- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected version(s)

You can expect an acknowledgement within **72 hours** and a fix or mitigation plan within **14 days** for confirmed issues.

## Scope

Areas most relevant to security review:

- **API key storage** — NexusMods key (XOR+base64 in JSON config), Claude key (AES-256-GCM via system keyring or encrypted file in `gui/secret_store.py`)
- **NexusMods free-user download** — browser cookie extraction from Firefox/Chromium SQLite databases (`gui/nexusmods_client.py`)
- **File parsing** — binary `.strings`/`.dlstrings`/`.ilstrings` parser, BA2 archive reader, ESP/ESM plugin parser (`bethesda_strings/`)
- **Archive extraction** — ZIP/7z/RAR extraction from downloaded mod files (`gui/nexusmods_browser_dialog.py`)
- **Audit log** — append-only JSON-lines log; must never record string content (`gui/audit_log.py`)

## Out of Scope

- Vulnerabilities in third-party dependencies (report to the respective upstream project)
- Issues requiring physical access to the machine
- Social engineering
