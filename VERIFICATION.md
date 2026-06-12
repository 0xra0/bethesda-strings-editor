# Verifying Release Files

Every release ships three extra files alongside the `.zip` archives:

| File | Purpose |
|---|---|
| `SHA256SUMS` | SHA-256 checksums of all release archives |
| `SHA256SUMS.asc` | Detached GPG signature over `SHA256SUMS` |
| `release-signing-key.asc` | Project public key (also below) |

---

## Quick verification (one copy-paste)

```bash
# 1. Import the project signing key
gpg --import release-signing-key.asc

# 2. Verify the checksum file is authentic
gpg --verify SHA256SUMS.asc SHA256SUMS

# 3. Verify your downloaded archive matches the checksum
sha256sum --check --ignore-missing SHA256SUMS
```

All three commands should succeed with no errors. `gpg --verify` will print
`Good signature from "Bethesda Strings Editor Releases"`.

---

## Key details

```
pub   rsa4096 2026-06-12 [SC] [expires: 2030-06-11]
      D50C 3274 546F E1FB 0653  DA01 E750 D9A9 4177 134B
uid   Bethesda Strings Editor Releases <REDACTED>
```

Full fingerprint: `D50C3274546FE1FB0653DA01E750D9A94177134B`

The public key is committed to this repository as `release-signing-key.asc`
and can be fetched directly:

```bash
gpg --fetch-keys \
  https://raw.githubusercontent.com/0xra0/bethesda-strings-editor/main/release-signing-key.asc
```

---

## Manual step-by-step

```bash
# Import key from repo (or from the release assets)
gpg --import release-signing-key.asc

# Optionally, confirm the fingerprint matches the one above
gpg --fingerprint REDACTED

# Verify signature — "Good signature" = checksums are untampered
gpg --verify SHA256SUMS.asc SHA256SUMS

# Check your specific file, e.g. Linux build
sha256sum -c SHA256SUMS --ignore-missing
# Expected output:  bethesda-strings-editor-linux-x64.zip: OK
```

---

## Why this matters

The ZIP archives are built by GitHub Actions on isolated runners and signed
with a key whose private half never leaves the CI environment. If an attacker
were to tamper with a release asset after upload, the SHA-256 checksum would
not match. If they replaced `SHA256SUMS` itself, the GPG signature would fail.
Both checks together mean you can trust that what you downloaded is exactly
what was built from the source code at the tagged commit.
