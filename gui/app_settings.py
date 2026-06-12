"""
Centralized application settings with validation, versioning, and migration.
Replaces scattered QSettings usage with a single typed config object.
"""

import base64
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)

CONFIG_VERSION = 29  # Increment when schema changes

# Fields whose values are XOR-obfuscated with base64 in the on-disk JSON.
# The in-memory value is always plaintext; only the serialized form is wrapped.
# Prefix "enc:" distinguishes obfuscated values from legacy plaintext entries.
_OBFUSCATED_FIELDS = {"nexusmods_api_key"}
_OBF_SALT = b"bethesda_strings_ed_v1"


def _obfuscate(plaintext: str) -> str:
    if not plaintext:
        return plaintext
    data = plaintext.encode("utf-8")
    salt = (_OBF_SALT * (len(data) // len(_OBF_SALT) + 1))[: len(data)]
    return "enc:" + base64.b64encode(bytes(a ^ b for a, b in zip(data, salt))).decode()


def _deobfuscate(value: str) -> str:
    if not value or not value.startswith("enc:"):
        return value  # legacy plaintext — use as-is
    try:
        raw = base64.b64decode(value[4:])
        salt = (_OBF_SALT * (len(raw) // len(_OBF_SALT) + 1))[: len(raw)]
        return bytes(a ^ b for a, b in zip(raw, salt)).decode("utf-8")
    except Exception:
        return ""


@dataclass
class AppSettings:
    """
    All application settings in one place.

    This is the single source of truth for defaults, types, and validation.
    Settings are persisted to both QSettings (for UI state) and a JSON file
    (for human editing, sharing, and versioning).
    """

    # ── Config metadata ──────────────────────────────────────────
    config_version: int = CONFIG_VERSION

    # ── Ollama ───────────────────────────────────────────────────
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "translategemma3-st"
    ollama_num_predict: int = 4096
    ollama_num_ctx: int = 16384

    # ── Translation defaults ─────────────────────────────────────
    default_source_lang: str = "ru"   # Starfield locale code (en/de/es/fr/it/ja/pl/ptbr/zhhans/ru/uk)
    default_target_lang: str = "uk"
    quality_level: int = 7
    long_string_threshold: int = 1000
    long_string_action: str = "Translate"  # Options: Translate, Original, Skip

    # ── Term protection ──────────────────────────────────────────
    enable_term_protection: bool = True
    protect_english_text: bool = False
    protect_named_entities: bool = False  # faction/company/ship/character/lore terms
    protected_terms_file: str = ""

    # ── Appearance ───────────────────────────────────────────────
    theme: str = "Slate"
    ui_language: str = "en"  # BCP-47 locale code, e.g. "en", "uk_UA", "de_DE"
    font_size: int = 0          # 0 = follow OS default; 8-24 = explicit pt size
    color_blind_mode: bool = False  # replace red/green with blue/orange in status column

    # ── Security ─────────────────────────────────────────────────────
    encrypt_cache: bool = False     # AES-256-GCM at-rest encryption for translation cache
    audit_logging: bool = False     # append-only JSON-lines security event log

    # ── Behavior ─────────────────────────────────────────────────
    auto_save: bool = False

    # ── Performance ──────────────────────────────────────────────
    enable_cache: bool = True
    max_workers: int = 10
    ollama_num_thread: int = 0  # 0 = auto (let Ollama decide)

    # ── Pre-translation estimation ───────────────────────────────
    enable_pre_translation_estimate: bool = True

    # ── Glossary ─────────────────────────────────────────────────
    enable_glossary: bool = True

    # ── Lore RAG ──────────────────────────────────────────────────
    enable_lore_rag: bool = False   # off by default until user downloads lore data
    lore_rag_max_snippet_chars: int = 480  # max chars of lore context per prompt

    # ── Keyboard shortcuts ────────────────────────────────────────────────
    custom_shortcuts: dict = field(default_factory=dict)

    # ── Translation Memory ────────────────────────────────────────────────
    # max_score for fuzzy TM lookup (xTranslator distance — lower = stricter).
    # 0 = exact only, 3 = loose (default), 5 = very loose.
    tm_fuzzy_max_score: float = 3.0

    # ── NexusMods ────────────────────────────────────────────────────────
    nexusmods_api_key: str = ""
    nexusmods_file_group_id: str = ""
    nexusmods_cookies_file: str = ""  # path to Cookie-Editor JSON export (free-user downloads)

    # ── Help ─────────────────────────────────────────────────────────────
    tips_shown: bool = False

    # ── Recent files ──────────────────────────────────────────────────────
    recent_files: list = field(default_factory=list)

    # ── AI Quality Check ──────────────────────────────────────────────────────
    enable_ai_qc: bool = False
    ai_qc_model: str = "qcgemma4-st"

    # ── String-type skipping ──────────────────────────────────────────────────
    # List of StringType names (e.g. ["BOOK", "NOTE"]) to skip during AI
    # translation batch.  Strings of skipped types are left untranslated.
    skip_string_types: list = field(default_factory=list)


    # ── Audio / TTS Preview ───────────────────────────────────────────────────
    enable_audio_preview: bool = False
    tts_engine_type: str = "espeak"   # "espeak" | "piper" | "none"
    espeak_voice: str = "uk"          # espeak-ng voice code
    espeak_binary: str = "espeak-ng"
    espeak_speed: int = 130
    piper_binary: str = ""
    piper_model: str = ""
    audio_dir: str = ""               # root directory of extracted game audio files
    tts_auto_preview: bool = False    # auto-synthesize on selection change

    # ── Validation rules ─────────────────────────────────────────
    _URL_MIN_LENGTH = 5
    _QUALITY_MIN, _QUALITY_MAX = 1, 10

    @classmethod
    def defaults(cls) -> "AppSettings":
        """Factory for default settings."""
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        """Create settings from a dict, applying migration if needed."""
        # Remove unknown keys
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys and v is not None}

        # Decode obfuscated fields before constructing
        for field_name in _OBFUSCATED_FIELDS:
            if field_name in filtered and isinstance(filtered[field_name], str):
                filtered[field_name] = _deobfuscate(filtered[field_name])

        # Migrate if version is old
        version = filtered.get("config_version", 1)
        if version < CONFIG_VERSION:
            filtered = _migrate_config(filtered, version)

        return cls(**filtered)

    def to_dict(self) -> dict:
        """Convert to a serializable dict (sensitive fields obfuscated)."""
        d = asdict(self)
        for field_name in _OBFUSCATED_FIELDS:
            if d.get(field_name):
                d[field_name] = _obfuscate(d[field_name])
        return d

    def validate(self) -> list[str]:
        """Validate all settings. Returns list of error messages (empty = valid)."""
        errors = []

        # Ollama URL validation
        if not self.ollama_url:
            errors.append("Ollama URL cannot be empty")
        elif len(self.ollama_url) < self._URL_MIN_LENGTH:
            errors.append(f"Ollama URL too short (min {self._URL_MIN_LENGTH} chars)")
        elif not self.ollama_url.startswith(("http://", "https://")):
            errors.append("Ollama URL must start with http:// or https://")

        # Quality range
        if not (self._QUALITY_MIN <= self.quality_level <= self._QUALITY_MAX):
            errors.append(
                f"Quality must be between {self._QUALITY_MIN} and {self._QUALITY_MAX}"
            )

        # Theme name (validated at runtime against available themes)

        # Terms file path
        if self.protected_terms_file:
            terms_path = Path(self.protected_terms_file)
            if not terms_path.exists():
                errors.append(
                    f"Protected terms file not found: {self.protected_terms_file}"
                )

        return errors

    def apply_env_overrides(self) -> None:
        """Override settings with environment variables if set."""
        env_map = {
            "OLLAMA_URL": "ollama_url",
            "OLLAMA_MODEL": "ollama_model",
            "DEFAULT_THEME": "theme",
        }
        for env_var, attr_name in env_map.items():
            val = os.environ.get(env_var)
            if val:
                old = getattr(self, attr_name)
                setattr(self, attr_name, val)
                logger.info(f"Env override: {env_var}={val} (was: {old})")


# ── Migration ─────────────────────────────────────────────────────────


def _migrate_config(data: dict, from_version: int) -> dict:
    """Migrate config dict from old version to current."""
    if from_version < 2:
        # v1 → v2: Added config_version, protect_english_text, auto_save
        data["config_version"] = CONFIG_VERSION  # Always set to current
        data.setdefault("protect_english_text", False)
        data.setdefault("auto_save", False)
        logger.info("Migrated config from v1 to v2")

    if from_version < 3:
        # v2 → v3: Added enable_cache, max_workers, ollama_num_thread
        data["config_version"] = CONFIG_VERSION
        data.setdefault("enable_cache", True)
        data.setdefault("max_workers", 10)
        data.setdefault("ollama_num_thread", 0)
        logger.info("Migrated config from v2 to v3")

    if from_version < 4:
        # v3 → v4: Added ollama_num_predict, ollama_num_ctx, long_string_threshold, long_string_action
        data["config_version"] = CONFIG_VERSION
        data.setdefault("ollama_num_predict", 1024)
        data.setdefault("ollama_num_ctx", 4096)
        data.setdefault("long_string_threshold", 1000)
        data.setdefault("long_string_action", "Translate")
        logger.info("Migrated config from v3 to v4")

    if from_version < 5:
        # v4 → v5: Added ui_language
        data["config_version"] = CONFIG_VERSION
        data.setdefault("ui_language", "Ukrainian")
        logger.info("Migrated config from v4 to v5")

    if from_version < 6:
        # v5 → v6: (placeholder — no schema changes in this version)
        data["config_version"] = CONFIG_VERSION
        logger.info("Migrated config from v5 to v6")

    if from_version < 7:
        # v6 → v7: Increased default limits for long strings
        data["config_version"] = CONFIG_VERSION
        if data.get("ollama_num_predict") == 1024:
            data["ollama_num_predict"] = 4096
        if data.get("ollama_num_ctx") == 4096:
            data["ollama_num_ctx"] = 16384
        logger.info("Migrated config from v6 to v7")

    if from_version < 10:
        data["config_version"] = CONFIG_VERSION
        data.setdefault("enable_pre_translation_estimate", True)
        logger.info("Migrated config from v9 to v10")

    if from_version < 11:
        data["config_version"] = CONFIG_VERSION
        data.setdefault("enable_glossary", True)
        logger.info("Migrated config to v11")

    if from_version < 12:
        data["config_version"] = CONFIG_VERSION
        data.setdefault("custom_shortcuts", {})
        logger.info("Migrated config to v12")

    if from_version < 13:
        data["config_version"] = CONFIG_VERSION
        data.pop("qa_fix_model", None)
        logger.info("Migrated config to v13")

    if from_version < 14:
        data["config_version"] = CONFIG_VERSION
        data.setdefault("tips_shown", False)
        logger.info("Migrated config to v14")

    if from_version < 15:
        data["config_version"] = CONFIG_VERSION
        data.setdefault("tm_fuzzy_max_score", 3.0)
        logger.info("Migrated config to v15")

    if from_version < 16:
        data["config_version"] = CONFIG_VERSION
        data.setdefault("recent_files", [])
        logger.info("Migrated config to v16")

    if from_version < 17:
        # Migrate ui_language from English display names to locale codes
        _name_to_locale = {
            "Ukrainian": "uk_UA",
            "English": "en",
            "Spanish": "es_ES",
            "French": "fr_FR",
            "German": "de_DE",
            "Polish": "pl_PL",
            "Czech": "cs_CZ",
        }
        lang = data.get("ui_language", "en")
        data["ui_language"] = _name_to_locale.get(lang, lang)
        data["config_version"] = CONFIG_VERSION
        logger.info("Migrated config to v17: ui_language=%s", data["ui_language"])

    if from_version < 18:
        data.setdefault("font_size", 0)
        data.setdefault("color_blind_mode", False)
        data["config_version"] = CONFIG_VERSION
        logger.info("Migrated config to v18")

    if from_version < 19:
        data.setdefault("encrypt_cache", False)
        data.setdefault("audit_logging", False)
        data["config_version"] = CONFIG_VERSION
        logger.info("Migrated config to v19")

    if from_version < 20:
        # Language fields changed from display names ("Russian", "Ukrainian", "English")
        # to Starfield locale codes ("ru", "uk", "en") to support all official languages.
        _lang_name_to_code = {
            "English": "en", "German": "de", "Spanish": "es", "French": "fr",
            "Italian": "it", "Japanese": "ja", "Korean": "ko", "Polish": "pl",
            "Portuguese (Brazil)": "ptbr", "Chinese (Simplified)": "zhhans",
            "Russian": "ru", "Ukrainian": "uk",
        }
        for key in ("default_source_lang", "default_target_lang"):
            old_val = data.get(key, "")
            if old_val in _lang_name_to_code:
                data[key] = _lang_name_to_code[old_val]
        data["config_version"] = CONFIG_VERSION
        logger.info("Migrated config to v20: language codes → locale codes")

    if from_version < 21:
        data.setdefault("nexusmods_api_key", "")
        data.setdefault("nexusmods_file_group_id", "")
        data["config_version"] = CONFIG_VERSION
        logger.info("Migrated config to v21: added NexusMods upload settings")

    if from_version < 22:
        data.setdefault("enable_ai_qc", False)
        data.setdefault("ai_qc_model", "qcgemma4-st")
        data["config_version"] = CONFIG_VERSION
        logger.info("Migrated config to v22: added AI QC settings")

    if from_version < 23:
        data.setdefault("protect_named_entities", False)
        data["config_version"] = CONFIG_VERSION
        logger.info("Migrated config to v23: added protect_named_entities setting")

    if from_version < CONFIG_VERSION:
        logger.warning(
            f"Config version {from_version} is older than current {CONFIG_VERSION}. "
            f"Some settings may use defaults."
        )

    return data


# ── Persistence ─────────────────────────────────────────────────────────

# Default config file path (in user config dir alongside QSettings)
CONFIG_FILENAME = "config.json"

# Bootstrap file at the fixed default location — stores a single-line override path.
# Must always live at the hardcoded default dir so it's findable before we know the override.
_OVERRIDE_BOOTSTRAP = Path(os.path.expanduser("~/.config/BethesdaModTools/.config_dir_override"))
_DEFAULT_CONFIG_DIR = Path(os.path.expanduser("~/.config/BethesdaModTools"))


def get_config_dir_override() -> Optional[Path]:
    """Return the user-configured config dir, or None when using the default.

    Priority: BSE_CONFIG_DIR env var → bootstrap file → None (use default).
    """
    env = os.environ.get("BSE_CONFIG_DIR", "").strip()
    if env:
        return Path(env)
    try:
        if _OVERRIDE_BOOTSTRAP.exists():
            text = _OVERRIDE_BOOTSTRAP.read_text(encoding="utf-8").strip()
            if text:
                return Path(text)
    except OSError:
        pass
    return None


def set_config_dir_override(path: Optional[Path]) -> None:
    """Write or clear the config-dir override bootstrap file.

    Pass None to restore the default directory.
    """
    _OVERRIDE_BOOTSTRAP.parent.mkdir(parents=True, exist_ok=True)
    if path is None:
        try:
            _OVERRIDE_BOOTSTRAP.unlink()
        except FileNotFoundError:
            pass
    else:
        _OVERRIDE_BOOTSTRAP.write_text(str(path), encoding="utf-8")


def get_config_dir() -> Path:
    """Get the directory where the JSON config file is stored."""
    override = get_config_dir_override()
    if override:
        try:
            override.mkdir(parents=True, exist_ok=True)
            return override
        except OSError as e:
            logger.warning("Cannot use config dir override %s: %s — using default", override, e)
    _DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return _DEFAULT_CONFIG_DIR


_SSD_CACHE_DIR = Path("/mnt/ssd/bethesda-strings-editor")

# Bootstrap file for cache dir override — always at the fixed default location.
_CACHE_OVERRIDE_BOOTSTRAP = Path(os.path.expanduser("~/.config/BethesdaModTools/.cache_dir_override"))


def get_cache_dir_override() -> Optional[Path]:
    """Return the user-configured cache dir, or None when using the default.

    Priority: BSE_CACHE_DIR env var → bootstrap file → None (use default).
    """
    env = os.environ.get("BSE_CACHE_DIR", "").strip()
    if env:
        return Path(env)
    try:
        if _CACHE_OVERRIDE_BOOTSTRAP.exists():
            text = _CACHE_OVERRIDE_BOOTSTRAP.read_text(encoding="utf-8").strip()
            if text:
                return Path(text)
    except OSError:
        pass
    return None


def set_cache_dir_override(path: Optional[Path]) -> None:
    """Write or clear the cache-dir override bootstrap file.

    Pass None to restore the default selection logic (SSD → config dir).
    """
    _CACHE_OVERRIDE_BOOTSTRAP.parent.mkdir(parents=True, exist_ok=True)
    if path is None:
        try:
            _CACHE_OVERRIDE_BOOTSTRAP.unlink()
        except FileNotFoundError:
            pass
    else:
        _CACHE_OVERRIDE_BOOTSTRAP.write_text(str(path), encoding="utf-8")


def get_cache_dir() -> Path:
    """Return the directory for large cache files (e.g. translation cache).

    Priority: user override (BSE_CACHE_DIR / bootstrap file) → /mnt/ssd when
    mounted → config dir fallback.
    """
    override = get_cache_dir_override()
    if override:
        try:
            override.mkdir(parents=True, exist_ok=True)
            return override
        except OSError as e:
            logger.warning("Cannot use cache dir override %s: %s — using default", override, e)

    ssd_mount = Path("/mnt/ssd")
    if ssd_mount.is_mount():
        try:
            _SSD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            return _SSD_CACHE_DIR
        except OSError as e:
            logger.warning(
                "Cannot use SSD cache dir %s: %s — falling back to config dir",
                _SSD_CACHE_DIR,
                e,
            )
    return get_config_dir()


def get_config_path() -> Path:
    """Get full path to the JSON config file."""
    return get_config_dir() / CONFIG_FILENAME


def load_settings_json() -> AppSettings:
    """Load settings from JSON config file. Falls back to defaults on error."""
    config_path = get_config_path()

    if not config_path.exists():
        logger.info(f"No config file found at {config_path}, using defaults")
        return AppSettings.defaults()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        settings = AppSettings.from_dict(data)
        logger.info(f"Loaded settings from {config_path}")
        return settings
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file {config_path}: {e}")
        # Backup corrupt file
        backup = config_path.with_suffix(".json.bak")
        shutil.copy2(config_path, backup)
        logger.info(f"Backed up corrupt config to {backup}")
        return AppSettings.defaults()
    except Exception as e:
        logger.error(f"Failed to load settings from {config_path}: {e}")
        return AppSettings.defaults()


def save_settings_json(settings: AppSettings) -> bool:
    """Save settings to JSON config file. Returns True on success."""
    config_path = get_config_path()

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file, then rename
        tmp_path = config_path.with_suffix(".tmp")
        tmp_path.touch(mode=0o600)  # owner-only before any data is written
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings.to_dict(), f, indent=2)
        tmp_path.replace(config_path)
        config_path.chmod(0o600)  # re-apply after rename (umask may have changed it)

        logger.info(f"Saved settings to {config_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save settings to {config_path}: {e}")
        return False


def export_settings_json(filepath: Path, settings: AppSettings) -> bool:
    """Export settings to a specific JSON file."""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(settings.to_dict(), f, indent=2)
        logger.info(f"Exported settings to {filepath}")
        return True
    except Exception as e:
        logger.error(f"Failed to export settings to {filepath}: {e}")
        return False


def import_settings_json(filepath: Path) -> Optional[AppSettings]:
    """Import settings from a specific JSON file. Returns None on failure."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        settings = AppSettings.from_dict(data)
        errors = settings.validate()
        if errors:
            logger.warning(f"Imported settings have validation issues: {errors}")
        logger.info(f"Imported settings from {filepath}")
        return settings
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in import file {filepath}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to import settings from {filepath}: {e}")
        return None


# ── QSettings bridge ──────────────────────────────────────────────────


def load_settings_qsettings() -> AppSettings:
    """Load settings from QSettings (legacy). Used as fallback."""
    qs = QSettings()

    def _s(key: str, default: str) -> str:
        return str(qs.value(key, default))

    def _i(key: str, default: int) -> int:
        return int(qs.value(key, default, type=int))  # type: ignore[arg-type]

    def _b(key: str, default: bool) -> bool:
        return bool(qs.value(key, default, type=bool))  # type: ignore[arg-type]

    return AppSettings(
        config_version=CONFIG_VERSION,
        ollama_url=_s("ollama/url", "http://localhost:11434"),
        ollama_model=_s("ollama/model", "translategemma3-st"),
        ollama_num_predict=_i("ollama/num_predict", 4096),
        ollama_num_ctx=_i("ollama/num_ctx", 16384),
        default_source_lang=_s("translation/source_lang", "ru"),
        default_target_lang=_s("translation/target_lang", "uk"),
        quality_level=_i("translation/quality", 7),
        long_string_threshold=_i("translation/long_string_threshold", 1000),
        long_string_action=_s("translation/long_string_action", "Translate"),
        enable_term_protection=_b("protection/enabled", True),
        protect_english_text=_b("protection/protect_english_text", False),
        protected_terms_file=_s("protection/terms_file", ""),
        theme=_s("appearance/theme", "Slate"),
        ui_language=_s("appearance/ui_language", "en"),
        font_size=_i("appearance/font_size", 0),
        color_blind_mode=_b("appearance/color_blind_mode", False),
        encrypt_cache=_b("security/encrypt_cache", False),
        audit_logging=_b("security/audit_logging", False),
        auto_save=_b("behavior/auto_save", False),
        enable_cache=_b("performance/enable_cache", True),
        max_workers=_i("performance/max_workers", 10),
        ollama_num_thread=_i("performance/ollama_num_thread", 0),
        enable_pre_translation_estimate=_b("analysis/enable_pre_translation_estimate", True),
        enable_glossary=_b("glossary/enable_glossary", True),
    )


def save_settings_qsettings(settings: AppSettings) -> None:
    """Save settings to QSettings (for backward compatibility)."""
    qs = QSettings()
    qs.setValue("config_version", settings.config_version)
    qs.setValue("ollama/url", settings.ollama_url)
    qs.setValue("ollama/model", settings.ollama_model)
    qs.setValue("ollama/num_predict", settings.ollama_num_predict)
    qs.setValue("ollama/num_ctx", settings.ollama_num_ctx)
    qs.setValue("translation/source_lang", settings.default_source_lang)
    qs.setValue("translation/target_lang", settings.default_target_lang)
    qs.setValue("translation/quality", settings.quality_level)
    qs.setValue("translation/long_string_threshold", settings.long_string_threshold)
    qs.setValue("translation/long_string_action", settings.long_string_action)
    qs.setValue("protection/enabled", settings.enable_term_protection)
    qs.setValue("protection/protect_english_text", settings.protect_english_text)
    qs.setValue("protection/terms_file", settings.protected_terms_file)
    qs.setValue("appearance/theme", settings.theme)
    qs.setValue("appearance/ui_language", settings.ui_language)
    qs.setValue("appearance/font_size", settings.font_size)
    qs.setValue("appearance/color_blind_mode", settings.color_blind_mode)
    qs.setValue("security/encrypt_cache", settings.encrypt_cache)
    qs.setValue("security/audit_logging", settings.audit_logging)
    qs.setValue("behavior/auto_save", settings.auto_save)
    qs.setValue("performance/enable_cache", settings.enable_cache)
    qs.setValue("performance/max_workers", settings.max_workers)
    qs.setValue("performance/ollama_num_thread", settings.ollama_num_thread)
    qs.setValue("analysis/enable_pre_translation_estimate", settings.enable_pre_translation_estimate)
    qs.setValue("glossary/enable_glossary", settings.enable_glossary)
    qs.sync()  # Force flush to disk


def load_settings() -> AppSettings:
    """
    Load settings with priority:
    1. JSON config file (primary)
    2. QSettings (fallback for legacy)
    3. Defaults
    Then apply environment variable overrides.
    """
    config_path = get_config_path()
    if config_path.exists():
        settings = load_settings_json()
    else:
        # Try QSettings as fallback
        settings = load_settings_qsettings()

    # Apply environment overrides
    settings.apply_env_overrides()

    return settings


def save_settings(settings: AppSettings) -> bool:
    """Save settings to both JSON config file and QSettings."""
    json_ok = save_settings_json(settings)
    try:
        save_settings_qsettings(settings)
        qsettings_ok = True
    except Exception as e:
        logger.error(f"Failed to save QSettings: {e}")
        qsettings_ok = False

    return json_ok and qsettings_ok


# ── Theme helper ──────────────────────────────────────────────────────


def apply_theme(app, theme_name: str, theme_manager=None) -> bool:
    """Apply a theme to the application. Returns True if theme was found.

    Args:
        app: QApplication instance.
        theme_name: Name of the theme to apply.  "Auto (System)" is resolved
                    to the appropriate concrete theme for the current OS
                    color scheme before applying.
        theme_manager: Optional existing ThemeManager instance. A new one is
                       created only when this is None (avoids repeated file-system
                       scans when called from the main window).
    """
    if theme_manager is None:
        from gui.theme_manager import ThemeManager
        theme_manager = ThemeManager()

    concrete = theme_manager.effective_theme(theme_name)
    theme_manager.set_theme(theme_name)  # remember the logical name (may be Auto)

    stylesheet = theme_manager.get_stylesheet(concrete)
    if stylesheet:
        app.setStyleSheet(stylesheet)
        logger.info(f"Theme applied: {theme_name} → {concrete}")
        return True
    else:
        logger.warning(f"Theme not found: {concrete}, falling back to Slate")
        stylesheet = theme_manager.get_stylesheet("Slate")
        if stylesheet:
            app.setStyleSheet(stylesheet)
        return False
