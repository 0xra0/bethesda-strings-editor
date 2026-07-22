# Verifying Release Files

Every release ships two extra files alongside the `.zip` archives:

| File | Purpose |
|---|---|
| `SHA256SUMS` | SHA-256 checksums of the release archives |
| `SHA256SUMS.asc` | Detached GPG signature over `SHA256SUMS` |

The public key is **not** a release asset — it lives in this repository as
[`release-signing-key.asc`](release-signing-key.asc). That is deliberate: a key
published next to the files it signs proves nothing, because anyone able to
replace an asset could replace the key too. Take it from the repository, and
check the fingerprint below.

**Identify the key by its fingerprint, never by its name or address.** A user ID
is free text that anybody can put on a key they generated themselves; the
fingerprint is the key.

```
4DF8 BE08 A2CB 5E00 62BE  EBAC 1FD0 408A 426E 7AD0
```

Questions or a suspected problem with a release go to
[GitHub Security Advisories](https://github.com/0xra0/bethesda-strings-editor/security/advisories/new),
not to any address attached to the key.

---

## Quick verification (one copy-paste)

```bash
# 1. Import the project signing key (from your clone, or fetch it — see below)
gpg --import release-signing-key.asc

# 2. Verify the checksum file is authentic
gpg --verify SHA256SUMS.asc SHA256SUMS

# 3. Verify your downloaded archive matches the checksum
sha256sum --check --ignore-missing SHA256SUMS
```

Step 2 prints this shape — `Good signature`, then the fingerprint to check:

```
gpg: Signature made <date>
gpg:                using RSA key 4DF8BE08A2CB5E0062BEEBAC1FD0408A426E7AD0
gpg: Good signature from "Bethesda Strings Editor Releases …" [unknown]
gpg: WARNING: This key is not certified with a trusted signature!
gpg:          There is no indication that the signature belongs to the owner.
Primary key fingerprint: 4DF8 BE08 A2CB 5E00 62BE  EBAC 1FD0 408A 426E 7AD0
```

**That WARNING is expected and is not a failure.** `Good signature` is the
result; the warning only says you have not personally certified the key in your
own web of trust. What replaces that trust here is the fingerprint — check that
the `Primary key fingerprint` line matches the one above, character for
character. To silence the warning permanently:

```bash
gpg --lsign-key 4DF8BE08A2CB5E0062BEEBAC1FD0408A426E7AD0
```

Step 3 prints `bethesda-strings-editor-linux-x64.zip: OK` for each archive you
actually downloaded; `--ignore-missing` is what lets you check one archive
against a `SHA256SUMS` that lists both.

---

## Key details

```
pub   rsa4096 2026-07-22 [SC] [expires: 2030-07-21]
      4DF8 BE08 A2CB 5E00 62BE  EBAC 1FD0 408A 426E 7AD0
```

The key carries no email address — a user ID is free text and proves nothing,
so there is deliberately none to mistake for a contact address.

### Key history

| Key | Signed | Status |
|---|---|---|
| `4DF8 BE08 A2CB 5E00 62BE  EBAC 1FD0 408A 426E 7AD0` | v0.2.6 onwards | **current** — published here |
| `D50C 3274 546F E1FB 0653  DA01 E750 D9A9 4177 134B` | up to v0.2.5 | retired, no longer published |

The retired key's public half is no longer distributed, so `SHA256SUMS.asc` on
**v0.2.5 and earlier cannot be signature-checked** any more. Their `SHA256SUMS`
still detects a corrupted or truncated download, but it no longer proves who
produced the file. Use v0.2.6 or later if you need the signature, or ask on the
issue tracker and those releases can be re-signed with the current key.

Fetch the key directly instead of cloning:

```bash
gpg --fetch-keys \
  https://raw.githubusercontent.com/0xra0/bethesda-strings-editor/main/release-signing-key.asc
```

---

## Manual step-by-step

```bash
# Fetch the checksums for a specific release (or download them from the
# release page in a browser). Use a v0.2.6-or-later tag — see Key history.
gh release download vX.Y.Z -R 0xra0/bethesda-strings-editor -p 'SHA256SUMS*'

# Import the key from your clone of the repo
gpg --import release-signing-key.asc

# Confirm the fingerprint matches the one above
gpg --fingerprint 4DF8BE08A2CB5E0062BEEBAC1FD0408A426E7AD0

# Verify the signature — "Good signature" = the checksum list is untampered
gpg --verify SHA256SUMS.asc SHA256SUMS

# Check your specific file, e.g. the Linux build
sha256sum -c SHA256SUMS --ignore-missing
# Expected output:  bethesda-strings-editor-linux-x64.zip: OK
```

---

## If you used the in-app updater

**Help → Check for Updates** downloads the release `.zip` over HTTPS to your
`Downloads` folder and opens the folder — it never extracts or runs anything,
and it does **not** check the file against `SHA256SUMS`. Verify it yourself with
the steps above before unpacking.

---

## Why this matters

The ZIP archives are built by GitHub Actions on isolated runners and signed with
a key whose private half never leaves the CI environment (it is stored as the
`GPG_PRIVATE_KEY` repository secret and imported into the runner for one step).
If an attacker tampered with a release asset after upload, its SHA-256 checksum
would no longer match. If they replaced `SHA256SUMS` itself, the GPG signature
would fail. Both checks together mean what you downloaded is what was built from
the source at the tagged commit.

What this does **not** prove: that the source at that commit is free of bugs, or
that the Windows binary is Authenticode-signed (it is not yet — see the
commented-out SignPath steps in `.github/workflows/release.yml`), so SmartScreen
may still warn on first run. Linux ELF binaries cannot be Authenticode-signed at
all; the GPG-signed checksums are their integrity story.
