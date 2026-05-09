from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from core.path_utils import get_app_dir

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "ko", "ja", "es", "pt-BR", "zh-CN")
_LANGUAGE_INDEX = {code.casefold(): code for code in SUPPORTED_LANGUAGES}

_active_language = DEFAULT_LANGUAGE
_fallback_language = DEFAULT_LANGUAGE
_locale_cache: dict[str, dict[str, Any]] = {}


def normalize_language_code(language: object, default: str = DEFAULT_LANGUAGE) -> str:
    if not isinstance(language, str):
        return default
    candidate = language.strip().replace("_", "-")
    if not candidate:
        return default
    return _LANGUAGE_INDEX.get(candidate.casefold(), default)


def list_supported_languages() -> tuple[str, ...]:
    return SUPPORTED_LANGUAGES


def set_locale(language: object, fallback_language: object | None = None) -> None:
    global _active_language, _fallback_language
    _active_language = normalize_language_code(language, DEFAULT_LANGUAGE)
    _fallback_language = normalize_language_code(fallback_language, DEFAULT_LANGUAGE)


def get_locale() -> tuple[str, str]:
    return _active_language, _fallback_language


def _get_locale_search_paths() -> list[Path]:
    app_dir = get_app_dir()
    source_root = Path(__file__).resolve().parents[1]
    paths: list[Path] = []
    for candidate in (app_dir / "locales", source_root / "locales"):
        if candidate not in paths:
            paths.append(candidate)
    return paths


def _load_locale_data(language: str) -> dict[str, Any]:
    cached = _locale_cache.get(language)
    if cached is not None:
        return cached

    loaded: dict[str, Any] = {}
    for locale_dir in _get_locale_search_paths():
        locale_file = locale_dir / f"{language}.yaml"
        if not locale_file.exists():
            continue
        try:
            raw = yaml.safe_load(locale_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                loaded = raw
            else:
                logger.warning("Locale file is not a dict: %s", locale_file)
                loaded = {}
            break
        except Exception as exc:
            logger.warning("Failed to load locale file %s: %s", locale_file, exc)

    _locale_cache[language] = loaded
    return loaded


def _resolve_key(data: dict[str, Any], key: str) -> str | None:
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if isinstance(current, str):
        return current
    if current is None:
        return None
    return str(current)


def t(key: str, **kwargs: object) -> str:
    for language in (_active_language, _fallback_language, DEFAULT_LANGUAGE):
        message = _resolve_key(_load_locale_data(language), key)
        if message is None:
            continue
        if not kwargs:
            return message
        try:
            return message.format(**kwargs)
        except Exception:
            logger.debug("Failed to format i18n key '%s' with args: %s", key, kwargs)
            return message
    return key
