"""
Thread-safe translation cache with LRU eviction, autosave, and hit/miss stats.
"""

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE = 100_000
DEFAULT_AUTOSAVE_INTERVAL = 200  # save after every N new entries


class TranslationCache:
    """
    Thread-safe LRU cache for translation results keyed by a hash of the
    source text plus the relevant settings (model, source/target language).

    Entries are evicted in least-recently-used order when the cache is full.
    A background autosave triggers every *autosave_interval* new entries so
    progress survives crashes without a constant flush-to-disk overhead.

    All public methods are safe to call from multiple threads concurrently.
    """

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        max_size: int = DEFAULT_MAX_SIZE,
        autosave_interval: int = DEFAULT_AUTOSAVE_INTERVAL,
    ):
        self._lock = threading.Lock()
        self._data: OrderedDict[str, str] = OrderedDict()
        self._cache_path = cache_path
        self._max_size = max(1, max_size)
        self._autosave_interval = autosave_interval

        self._hits = 0
        self._misses = 0
        self._new_since_save = 0

        if cache_path:
            self.load()

    # ── Persistence ───────────────────────────────────────────────

    def load(self) -> None:
        """Load cache from disk.  Silently ignores missing or corrupt files."""
        if not self._cache_path or not self._cache_path.exists():
            return
        try:
            with open(self._cache_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                with self._lock:
                    self._data = OrderedDict(raw)
                    self._new_since_save = 0
                logger.info(
                    "Translation cache loaded: %d entries from %s",
                    len(self._data),
                    self._cache_path,
                )
        except Exception as e:
            logger.warning("Could not load translation cache from %s: %s", self._cache_path, e)

    def save(self) -> None:
        """Persist cache to disk using an atomic write."""
        if not self._cache_path:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                snapshot = dict(self._data)
            tmp = self._cache_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, ensure_ascii=False, indent=None, separators=(",", ":"))
            tmp.replace(self._cache_path)
            logger.info(
                "Translation cache saved: %d entries to %s", len(snapshot), self._cache_path
            )
        except Exception as e:
            logger.error("Failed to save translation cache to %s: %s", self._cache_path, e)

    # ── Cache access ──────────────────────────────────────────────

    @staticmethod
    def make_key(
        original_text: str, model: str, source_lang: str, target_lang: str
    ) -> str:
        """Build a stable cache key from the relevant request parameters."""
        raw = f"{model}\x00{source_lang}\x00{target_lang}\x00{original_text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[str]:
        """Return the cached translation for *key*, or ``None`` if not present.

        Marks the entry as recently used (LRU promotion).
        """
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
                self._hits += 1
                return value
            self._misses += 1
            return None

    def set(self, key: str, translated: str) -> None:
        """Store *translated* under *key*.

        When full, evicts the least-recently-used entry.  Triggers an autosave
        every *autosave_interval* new insertions.
        """
        do_autosave = False
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = translated
                return
            if len(self._data) >= self._max_size:
                self._data.popitem(last=False)  # evict LRU (oldest)
                logger.debug("Translation cache full, evicted LRU entry")
            self._data[key] = translated
            self._new_since_save += 1
            if self._autosave_interval > 0 and self._new_since_save >= self._autosave_interval:
                self._new_since_save = 0
                do_autosave = True

        if do_autosave:
            self.save()

    def delete(self, key: str) -> bool:
        """Remove a single entry by key.  Returns True if the key was present."""
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def clear(self) -> None:
        """Remove all cached entries (in memory only; call ``save()`` to persist)."""
        with self._lock:
            self._data.clear()
            self._hits = 0
            self._misses = 0
            self._new_since_save = 0
        logger.info("Translation cache cleared")

    # ── Statistics ────────────────────────────────────────────────

    def stats(self) -> Dict[str, object]:
        """Return a snapshot of cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._data),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / total if total else 0.0,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
