# Verifying Release Files

Every release ships two extra files alongside the `.zip` archives:

| File | Purpose |
|---|---|
| `SHA256SUMS` | SHA-256 checksums of the release archives |
| `SHA256SUMS.asc` | Detached GPG signature over `SHA256SUMS` |

The public key is **not** a release asset — it lives in this repository as
[`release-signing-key.asc`](release-signing-key.asc). That is deliberate: a key
published next to the files it signs proves nothing, because anyone able to
replace an asset could replace the key too. Take it from the repository (or a
keyserver), and check the fingerprint below.

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

Step 2 prints something like this — the exact output for the v0.2.5 release:

```
gpg: Signature made Mon 20 Jul 2026 10:45:12 AM EEST
gpg:                using RSA key D50C3274546FE1FB0653DA01E750D9A94177134B
gpg: Good signature from "Bethesda Strings Editor Releases <…>" [unknown]
gpg: WARNING: This key is not certified with a trusted signature!
gpg:          There is no indication that the signature belongs to the owner.
Primary key fingerprint: D50C 3274 546F E1FB 0653  DA01 E750 D9A9 4177 134B
```

**That WARNING is expected and is not a failure.** `Good signature` is the
result; the warning only says you have not personally certified the key in your
own web of trust. What replaces that trust here is the fingerprint: check that
the `Primary key fingerprint` line matches the one below. To silence the warning
permanently, sign the key locally with `gpg --lsign-key D50C3274546FE1FB0653DA01E750D9A94177134B`.

Step 3 prints `bethesda-strings-editor-linux-x64.zip: OK` for each archive you
actually downloaded; `--ignore-missing` is what lets you check one archive
against a `SHA256SUMS` listing both.

---

## Key details

```
pub   rsa4096 2026-06-12 [SC] [expires: 2030-06-11]
      D50C 3274 546F E1FB 0653  DA01 E750 D9A9 4177 134B
uid   Bethesda Strings Editor Releases <claude.85@friendlyshare.com.ua>
```

Full fingerprint: `D50C3274546FE1FB0653DA01E750D9A94177134B`

Fetch the key directly instead of cloning:

```bash
gpg --fetch-keys \
  https://raw.githubusercontent.com/0xra0/bethesda-strings-editor/main/release-signing-key.asc
```

---

## Manual step-by-step

```bash
# Fetch the checksums for a specific release (or download them from the
# release page in a browser)
gh release download v0.2.5 -R 0xra0/bethesda-strings-editor -p 'SHA256SUMS*'

# Import the key from your clone of the repo
gpg --import release-signing-key.asc

# Confirm the fingerprint matches the one above
gpg --fingerprint claude.85@friendlyshare.com.ua

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
