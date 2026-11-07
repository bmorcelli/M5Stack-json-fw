#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import json
import argparse
import math
import random
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ModuleNotFoundError:
    requests = None

SCRIPT_DIR = Path(__file__).resolve().parent

DEFAULT_INPUT = SCRIPT_DIR / "all_device_firmware.json"
DEFAULT_OUTPUT = DEFAULT_INPUT
DEFAULT_URL = "https://m5burner-api.m5stack.com/api/firmware"
DEFAULT_CACHE_FILE = SCRIPT_DIR / "translation_cache.json"
DEFAULT_ERRORS_FILE = SCRIPT_DIR / "translation_errors.json"

RE_TARGET_LANG = re.compile(
    r'['
    r'\u0400-\u04FF'   # Cyrillic
    r'\u0500-\u052F'   # Cyrillic Supplement
    r'\u2DE0-\u2DFF'   # Cyrillic Extended-A
    r'\uA640-\uA69F'   # Cyrillic Extended-B
    r'\u3040-\u309F'   # Hiragana
    r'\u30A0-\u30FF'   # Katakana
    r'\u31F0-\u31FF'   # Katakana Phonetic Extensions
    r'\u3400-\u4DBF'   # CJK Extension A
    r'\u4E00-\u9FFF'   # CJK Unified Ideographs
    r']'
)
RE_ASCII_WORD = re.compile(r"[A-Za-z]{2,}")
RE_TARGET_SEGMENT = re.compile(
    r'['
    r'\u0400-\u04FF'
    r'\u0500-\u052F'
    r'\u2DE0-\u2DFF'
    r'\uA640-\uA69F'
    r'\u3040-\u309F'
    r'\u30A0-\u30FF'
    r'\u31F0-\u31FF'
    r'\u3400-\u4DBF'
    r'\u4E00-\u9FFF'
    r']+'
)


# GET parameters percent-encode CJK characters as nine bytes each. Keep URLs
# small even when many short lines add repeated q parameters.
MAX_CHUNK_CHARS = 1000
DEFAULT_MAX_MINUTES = 40.0
MIN_REQUEST_INTERVAL = 5.0      # minimum pause after each completed request
CHECKPOINT_EVERY = 25           # save cache every N new translations
MAX_CONSECUTIVE_RATE_LIMITS = 6


class BudgetExceeded(Exception):
    """Raised when the time budget ends or the rate limit circuit opens."""


class TooManyRequests(Exception):
    """Provider answered HTTP 429."""

    def __init__(self, retry_after: float = 0.0) -> None:
        super().__init__("Google Translate HTTP 429")
        self.retry_after = retry_after


class GoogleTranslator:
    """Keyless Google Translate JSON endpoint (not the Cloud Translation API).

    This public endpoint has no guaranteed quota or availability. Keep requests
    serial and let GuardedTranslator handle throttling and HTTP 429 responses.
    """

    def __init__(self, source: str = "auto", target: str = "en") -> None:
        self.source = source
        self.target = target
        self.session = requests.Session()

    def translate(self, text: str) -> str:
        # The /t response flattens newlines inside a single q parameter. Send
        # each target-language line separately and restore blank/English lines
        # locally, without making an HTTP request for every line.
        lines = text.split("\n")
        indices = [i for i, line in enumerate(lines) if RE_TARGET_LANG.search(line)]
        if not indices:
            return text
        params = [("client", "dict-chrome-ex"), ("sl", self.source), ("tl", self.target)]
        params.extend(("q", lines[i]) for i in indices)
        with self.session.get(
            "https://translate.googleapis.com/translate_a/t",
            params=params,
            timeout=(10, 30),
        ) as response:
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "0")
                try:
                    seconds = float(retry_after)
                except ValueError:
                    try:
                        seconds = (parsedate_to_datetime(retry_after) -
                                   datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        seconds = 0.0
                raise TooManyRequests(max(0.0, seconds) if math.isfinite(seconds) else 0.0)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) != len(indices):
                raise ValueError("Unexpected Google Translate response")
            for index, part in zip(indices, payload):
                # Auto source: [[translation, detected_language], ...].
                # Explicit source: [translation, ...].
                translated = part[0] if isinstance(part, list) and part else part
                if not isinstance(translated, str) or not translated.strip():
                    raise ValueError("Empty or invalid Google Translate response")
                lines[index] = translated
            return "\n".join(lines)


class GuardedTranslator:
    """Wraps GoogleTranslator with throttling, rate-limit backoff, a time
    budget and periodic cache checkpoints, so an interrupted run keeps its
    progress and the next run resumes from the cache."""

    def __init__(self, translator: Any, cache: Dict[str, str], cache_path: Path,
                 max_seconds: float, min_interval: float = MIN_REQUEST_INTERVAL) -> None:
        self.translator = translator
        self.cache = cache
        self.cache_path = cache_path
        self.deadline = time.monotonic() + max_seconds
        self.last_request: Optional[float] = None
        self.interval = min_interval
        self.since_checkpoint = 0
        self.consecutive_rate_limits = 0
        self.stopped = False
        self.stop_reason = ""
        self.requests = 0

    def stop(self, reason: str) -> None:
        if not self.stopped:
            self.stopped = True
            self.stop_reason = reason
            print(f"[WARN] Stopping translation: {reason}", file=sys.stderr, flush=True)
        raise BudgetExceeded(reason)

    def checkpoint(self, force: bool = False) -> None:
        if force or self.since_checkpoint >= CHECKPOINT_EVERY:
            try:
                save_json_file(self.cache_path, self.cache)
            except Exception as exc:
                print(f"Warning: cache checkpoint failed: {exc}", file=sys.stderr)
            self.since_checkpoint = 0

    def translate(self, text: str) -> Optional[str]:
        while True:
            if self.stopped:
                raise BudgetExceeded(self.stop_reason)
            if time.monotonic() >= self.deadline:
                self.stop("time budget exhausted")

            wait = (self.interval - (time.monotonic() - self.last_request)
                    if self.last_request is not None else 0.0)
            if wait > 0:
                self.pause(wait)
            self.requests += 1

            try:
                result = self.translator.translate(text)
            except TooManyRequests as exc:
                self.consecutive_rate_limits += 1
                self.interval = max(self.interval, min(60.0, self.interval * 2))
                if self.consecutive_rate_limits >= MAX_CONSECUTIVE_RATE_LIMITS:
                    self.stop("rate limited by Google Translate")
                backoff = max(exc.retry_after, min(900, 60 * 2 ** (self.consecutive_rate_limits - 1)))
                backoff += random.uniform(0, 5)
                print(f"[WARN] Rate limited, sleeping {backoff:.1f}s; request interval {self.interval:.1f}s",
                      file=sys.stderr, flush=True)
                self.pause(backoff)
                continue
            finally:
                self.last_request = time.monotonic()

            self.consecutive_rate_limits = 0
            return result

    def pause(self, seconds: float) -> None:
        if seconds >= self.deadline - time.monotonic():
            self.stop("time budget insufficient for next request")
        time.sleep(seconds)
        if time.monotonic() >= self.deadline:
            self.stop("time budget exhausted")

    def cache_translation(self, original: str, translated: str) -> None:
        self.cache[original] = translated
        self.since_checkpoint += 1
        self.checkpoint()


def split_chunks(text: str, limit: int = MAX_CHUNK_CHARS) -> List[str]:
    """Split on line boundaries into chunks of at most `limit` chars."""
    chunks: List[str] = []
    current: List[str] = []
    size = 0
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        if current and size + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def translate_chunked(
    text: str,
    translator: GuardedTranslator,
    cache: Dict[str, str],
    stats: "Stats",
    errors: List[Dict[str, Any]],
    context: Dict[str, Any],
    retries: int,
    delay: float,
) -> Optional[str]:
    """Translate long text chunk by chunk. Each chunk is cached individually,
    so progress survives interruptions. Chunks without target-language text
    are kept as-is and cost no request."""
    out: List[str] = []
    failed = False
    for chunk in split_chunks(text):
        if not RE_TARGET_LANG.search(chunk):
            out.append(chunk)
            continue
        translated = translate_segment(
            segment=chunk,
            translator=translator,
            cache=cache,
            stats=stats,
            errors=errors,
            context=context,
            retries=retries,
            delay=delay,
        )
        if translated is None:
            failed = True
            out.append(chunk)
        else:
            out.append(translated)
    if failed:
        return None
    result = "\n".join(out)
    return result if result != text else None


class Stats:
    def __init__(self) -> None:
        self.updated_fields = 0
        self.cache_hits = 0
        self.new_translations = 0
        self.failed_translations = 0
        self.skipped_no_change = 0
        self.skipped_not_target_lang = 0


def log_runtime_error(message: str, error_type: str, context: Optional[Dict[str, Any]] = None) -> None:
    parts = [f"[ERROR] {error_type}: {message}"]
    if context:
        field = context.get("field")
        path = context.get("path")
        original = context.get("original")
        if field:
            parts.append(f"field={field}")
        if path:
            parts.append(f"path={path}")
        if isinstance(original, str):
            preview = original.replace("\n", "\\n")
            if len(preview) > 120:
                preview = preview[:117] + "..."
            parts.append(f"original={preview}")
    print(" | ".join(parts), file=sys.stderr, flush=True)


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: Path, data: Any, ensure_ascii: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=ensure_ascii, indent=2)
        f.write("\n")
    temporary.replace(path)


def resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def may_need_translation(text: Any) -> bool:
    if not isinstance(text, str):
        return False

    text = text.strip()
    if not text:
        return False

    if text.isascii():
        return False

    if max(map(ord, text)) <= 255:
        return False

    return bool(RE_TARGET_LANG.search(text))


def should_update_translation(item: Dict[str, Any], field: str) -> bool:
    original = item.get(field)
    if not isinstance(original, str):
        return False

    original = original.strip()
    if not original:
        return False

    if not may_need_translation(original):
        return False

    en_field = f"{field}_en"
    src_field = f"{field}_src"

    if en_field not in item:
        return True

    if src_field not in item:
        return True

    return item.get(src_field) != item.get(field)


def normalize_cache(cache_data: Any) -> Dict[str, str]:
    if not isinstance(cache_data, dict):
        return {}

    normalized: Dict[str, str] = {}
    for k, v in cache_data.items():
        if isinstance(k, str) and isinstance(v, str):
            normalized[k] = v
    return normalized


def count_target_chars(text: str) -> int:
    return len(RE_TARGET_LANG.findall(text))


def is_mixed_language_text(text: str) -> bool:
    return bool(RE_ASCII_WORD.search(text)) and bool(RE_TARGET_LANG.search(text))


def is_effectively_translated(original: str, translated: str, allow_mixed_result: bool = False) -> bool:
    translated = translated.strip()
    if not translated:
        return False

    if translated == original:
        return False

    original_target_chars = count_target_chars(original)
    translated_target_chars = count_target_chars(translated)
    ascii_word_count = len(RE_ASCII_WORD.findall(translated))

    if translated_target_chars == 0:
        return True

    if allow_mixed_result and translated_target_chars < original_target_chars:
        return True

    # Accept partial translations when the text clearly changed and now has
    # enough English content, even if some original names/terms remain.
    if ascii_word_count >= 3 and translated_target_chars < original_target_chars:
        return True

    return False


def translate_segment(
    segment: str,
    translator: GuardedTranslator,
    cache: Dict[str, str],
    stats: Stats,
    errors: List[Dict[str, Any]],
    context: Dict[str, Any],
    retries: int,
    delay: float,
) -> Optional[str]:
    cached = cache.get(segment)
    if cached is not None:
        stats.cache_hits += 1
        return cached

    last_error: Optional[str] = None

    for attempt in range(1, retries + 1):
        try:
            translated = translator.translate(segment)
            if translated is None:
                translated = ""

            translated = translated.strip()
            if translated and translated != segment:
                translator.cache_translation(segment, translated)
                stats.new_translations += 1
                return translated

            # Same input gives the same output; retrying only wastes quota.
            last_error = "Segment translation returned unchanged text"
            log_runtime_error(
                message=last_error,
                error_type="TranslationSegmentError",
                context={
                    "field": context.get("field"),
                    "path": context.get("path"),
                    "original": segment,
                },
            )
            break
        except BudgetExceeded:
            raise
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log_runtime_error(
                message=str(exc),
                error_type=type(exc).__name__,
                context={
                    "field": context.get("field"),
                    "path": context.get("path"),
                    "original": segment,
                },
            )
            if attempt < retries:
                time.sleep(delay)

    errors.append({
        "field": context.get("field"),
        "path": context.get("path"),
        "original": segment,
        "error": last_error,
    })
    return None


def translate_mixed_text(
    text: str,
    translator: GuardedTranslator,
    cache: Dict[str, str],
    stats: Stats,
    errors: List[Dict[str, Any]],
    context: Dict[str, Any],
    retries: int,
    delay: float,
) -> Optional[str]:
    matches = list(RE_TARGET_SEGMENT.finditer(text))
    if not matches:
        return None

    # Reuse old segment cache entries, but never send one request per fragment.
    # A cache miss is handled by translating the complete field once.
    if any(match.group(0) not in cache for match in matches):
        return None

    translated_text = text
    replacements: Dict[str, str] = {}

    for match in matches:
        segment = match.group(0)
        if segment in replacements:
            continue

        translated_segment = translate_segment(
            segment=segment,
            translator=translator,
            cache=cache,
            stats=stats,
            errors=errors,
            context=context,
            retries=retries,
            delay=delay,
        )
        if translated_segment is None:
            continue
        replacements[segment] = translated_segment

    if not replacements:
        return None

    for segment, translated_segment in replacements.items():
        translated_text = translated_text.replace(segment, translated_segment)

    if translated_text != text:
        return translated_text

    return None


def translate_to_english(
    text: str,
    translator: GuardedTranslator,
    cache: Dict[str, str],
    stats: Stats,
    errors: List[Dict[str, Any]],
    context: Dict[str, Any],
    retries: int = 3,
    delay: float = 2.0,
) -> Optional[str]:
    text = text.strip()

    cached = cache.get(text)
    if cached is not None:
        stats.cache_hits += 1
        return cached

    if len(text) > MAX_CHUNK_CHARS:
        chunked = translate_chunked(
            text=text,
            translator=translator,
            cache=cache,
            stats=stats,
            errors=errors,
            context=context,
            retries=retries,
            delay=delay,
        )
        if chunked is None:
            stats.failed_translations += 1
        elif chunked:
            translator.cache_translation(text, chunked)
        return chunked

    if is_mixed_language_text(text):
        mixed_translation = translate_mixed_text(
            text=text,
            translator=translator,
            cache=cache,
            stats=stats,
            errors=errors,
            context=context,
            retries=retries,
            delay=delay,
        )
        if mixed_translation and is_effectively_translated(text, mixed_translation, allow_mixed_result=True):
            translator.cache_translation(text, mixed_translation)
            return mixed_translation

    cached = cache.get(text)
    if cached is not None:
        stats.cache_hits += 1
        return cached

    last_error: Optional[str] = None

    for attempt in range(1, retries + 1):
        try:
            translated = translator.translate(text)
            if translated is None:
                translated = ""

            translated = translated.strip()

            if not is_effectively_translated(text, translated):
                last_error = "Translation returned unchanged or still contains target-language text"
                log_runtime_error(
                    message=last_error,
                    error_type="TranslationResultError",
                    context={
                        "field": context.get("field"),
                        "path": context.get("path"),
                        "original": text,
                    },
                )
                break

            translator.cache_translation(text, translated)
            stats.new_translations += 1
            return translated
        except BudgetExceeded:
            raise
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log_runtime_error(
                message=str(exc),
                error_type=type(exc).__name__,
                context={
                    "field": context.get("field"),
                    "path": context.get("path"),
                    "original": text,
                },
            )
            if attempt < retries:
                time.sleep(delay)

    stats.failed_translations += 1
    errors.append({
        "field": context.get("field"),
        "path": context.get("path"),
        "original": text,
        "error": last_error,
    })
    return None


def process_field(
    item: Dict[str, Any],
    field: str,
    path: str,
    translator: GuardedTranslator,
    cache: Dict[str, str],
    stats: Stats,
    errors: List[Dict[str, Any]],
) -> None:
    original = item.get(field)

    if not isinstance(original, str) or not original.strip():
        return

    if not may_need_translation(original):
        stats.skipped_not_target_lang += 1
        return

    if not should_update_translation(item, field):
        stats.skipped_no_change += 1
        return

    try:
        translated = translate_to_english(
            text=original,
            translator=translator,
            cache=cache,
            stats=stats,
            errors=errors,
            context={"field": field, "path": path},
        )
    except BudgetExceeded:
        return

    if translated is None:
        return

    item[f"{field}_src"] = original
    item[f"{field}_en"] = translated
    stats.updated_fields += 1


def walk(
    obj: Any,
    path: str,
    translator: GuardedTranslator,
    cache: Dict[str, str],
    stats: Stats,
    errors: List[Dict[str, Any]],
) -> None:
    if isinstance(obj, dict):
        for field in ("name", "description"):
            process_field(
                item=obj,
                field=field,
                path=path or "<root>",
                translator=translator,
                cache=cache,
                stats=stats,
                errors=errors,
            )

        for key, value in obj.items():
            child_path = f"{path}.{key}" if path else str(key)
            walk(
                obj=value,
                path=child_path,
                translator=translator,
                cache=cache,
                stats=stats,
                errors=errors,
            )

    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            child_path = f"{path}[{idx}]" if path else f"[{idx}]"
            walk(
                obj=value,
                path=child_path,
                translator=translator,
                cache=cache,
                stats=stats,
                errors=errors,
            )


def load_source_data(use_url: bool, input_path: Path) -> Any:
    if use_url:
        response = requests.get(DEFAULT_URL, timeout=60)
        response.raise_for_status()
        return response.json()

    if not input_path.exists():
        raise FileNotFoundError(f"File not found: {input_path}")

    with input_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    missing_modules: List[str] = []
    if requests is None:
        missing_modules.append("requests")

    if missing_modules:
        print(
            "Missing Python dependencies: "
            + ", ".join(missing_modules)
            + ". Install them with: python -m pip install "
            + " ".join(missing_modules),
            file=sys.stderr,
        )
        return 1

    parser = argparse.ArgumentParser(description="Translate firmware fields using keyless Google Translate.")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--url", action="store_true", help="Fetch the source catalog from M5Stack")
    parser.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                        help="Network time budget; 0 applies only cached translations")
    parser.add_argument("--min-interval", type=float, default=MIN_REQUEST_INTERVAL,
                        help="Minimum seconds between requests (default: 5)")
    options = parser.parse_args()
    if not math.isfinite(options.max_minutes) or options.max_minutes < 0:
        parser.error("--max-minutes must be finite and nonnegative")
    if not math.isfinite(options.min_interval) or options.min_interval < MIN_REQUEST_INTERVAL:
        parser.error(f"--min-interval must be finite and at least {MIN_REQUEST_INTERVAL}")
    args = options.paths
    use_url = options.url
    if len(args) > (1 if use_url else 2):
        parser.error("too many paths")

    if use_url:
        output_path = resolve_path(args[0]) if len(args) >= 1 else DEFAULT_OUTPUT
        input_path = DEFAULT_INPUT
    else:
        input_path = resolve_path(args[0]) if len(args) >= 1 else DEFAULT_INPUT
        output_path = resolve_path(args[1]) if len(args) >= 2 else input_path

    cache_path = DEFAULT_CACHE_FILE
    errors_path = DEFAULT_ERRORS_FILE

    stats = Stats()
    errors: List[Dict[str, Any]] = []

    try:
        data = load_source_data(use_url=use_url, input_path=input_path)
    except Exception as exc:
        print(f"Fatal error loading source JSON: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    cache = normalize_cache(load_json_file(cache_path, {}))
    initial_cache_size = len(cache)

    backend = GoogleTranslator(source="auto", target="en")
    print(f"Translator: Google (/translate_a/t, keyless); minimum interval {options.min_interval}s")

    translator = GuardedTranslator(
        translator=backend,
        cache=cache,
        cache_path=cache_path,
        max_seconds=options.max_minutes * 60,
        min_interval=options.min_interval,
    )

    started = time.perf_counter()

    walk(
        obj=data,
        path="",
        translator=translator,
        cache=cache,
        stats=stats,
        errors=errors,
    )

    elapsed = time.perf_counter() - started

    try:
        save_json_file(output_path, data, ensure_ascii=True)
    except Exception as exc:
        print(f"Fatal error writing output JSON: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        save_json_file(cache_path, cache)
    except Exception as exc:
        print(f"Warning: failed to save cache file: {type(exc).__name__}: {exc}", file=sys.stderr)

    try:
        save_json_file(errors_path, errors)
    except Exception as exc:
        print(f"Warning: failed to save errors file: {type(exc).__name__}: {exc}", file=sys.stderr)

    print("Done.")
    print(f"Updated fields: {stats.updated_fields}")
    print(f"Cache hits: {stats.cache_hits}")
    print(f"New translations saved to cache: {stats.new_translations}")
    print(f"Failed translations: {stats.failed_translations}")
    print(f"Skipped (no change): {stats.skipped_no_change}")
    print(f"Skipped (not target language): {stats.skipped_not_target_lang}")
    print(f"Initial cache size: {initial_cache_size}")
    print(f"Final cache size: {len(cache)}")
    print(f"Requests sent: {translator.requests}")
    if translator.stopped:
        print(f"Stopped early ({translator.stop_reason}); remaining work resumes next run from cache")
    print(f"Elapsed processing time: {elapsed:.3f}s")
    print(f"Output saved to: {output_path}")
    print(f"Cache saved to: {cache_path}")
    print(f"Errors saved to: {errors_path}")

    # Não falha o job por erro parcial de tradução.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
