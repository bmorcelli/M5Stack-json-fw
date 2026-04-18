#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ModuleNotFoundError:
    requests = None

try:
    from deep_translator import GoogleTranslator
except ModuleNotFoundError:
    GoogleTranslator = None


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
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=ensure_ascii, indent=2)
        f.write("\n")


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
    translator: GoogleTranslator,
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
                cache[segment] = translated
                stats.new_translations += 1
                return translated

            last_error = "Segment translation returned unchanged text"
            if attempt < retries:
                time.sleep(delay)
                continue
            log_runtime_error(
                message=last_error,
                error_type="TranslationSegmentError",
                context={
                    "field": context.get("field"),
                    "path": context.get("path"),
                    "original": segment,
                },
            )
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
    translator: GoogleTranslator,
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
    translator: GoogleTranslator,
    cache: Dict[str, str],
    stats: Stats,
    errors: List[Dict[str, Any]],
    context: Dict[str, Any],
    retries: int = 3,
    delay: float = 2.0,
) -> Optional[str]:
    text = text.strip()

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
                if attempt < retries:
                    time.sleep(delay)
                    continue
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

            cache[text] = translated
            stats.new_translations += 1
            return translated
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
    translator: GoogleTranslator,
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

    translated = translate_to_english(
        text=original,
        translator=translator,
        cache=cache,
        stats=stats,
        errors=errors,
        context={"field": field, "path": path},
    )

    if translated is None:
        return

    item[f"{field}_src"] = original
    item[f"{field}_en"] = translated
    stats.updated_fields += 1


def walk(
    obj: Any,
    path: str,
    translator: GoogleTranslator,
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
    if GoogleTranslator is None:
        missing_modules.append("deep-translator")

    if missing_modules:
        print(
            "Missing Python dependencies: "
            + ", ".join(missing_modules)
            + ". Install them with: python -m pip install "
            + " ".join(missing_modules),
            file=sys.stderr,
        )
        return 1

    args = sys.argv[1:]

    use_url = False
    if args and args[0] == "--url":
        use_url = True
        args = args[1:]

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

    translator = GoogleTranslator(source="auto", target="en")

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
    print(f"Elapsed processing time: {elapsed:.3f}s")
    print(f"Output saved to: {output_path}")
    print(f"Cache saved to: {cache_path}")
    print(f"Errors saved to: {errors_path}")

    # Não falha o job por erro parcial de tradução.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
