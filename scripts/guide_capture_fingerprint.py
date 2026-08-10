"""Deterministic freshness fingerprints for generated user-guide screenshots.

The module intentionally uses only the Python standard library so both the
offscreen exporter and the guide validator can share exactly the same
calculation.  A fingerprint covers the scenario contract, the concrete Qt
capture handler, and every explicitly mapped UI source file.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import struct
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


CAPTURE_FINGERPRINT_SCHEMA_VERSION = 1
CAPTURE_MANIFEST_SCHEMA_VERSION = 2
DEFAULT_HANDLER_SOURCE = "qa/offscreen/harness.py"
DEFAULT_HANDLER_CLASS = "OffscreenQaHarness"
CAPTURE_PIPELINE_METHODS = (
    "_run_scenario",
    "_navigate_main",
    "_click_list_row",
    "_flush",
    "_capture",
)
HANDLER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
OBJECT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TEXT_SOURCE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".spec",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


class CaptureFingerprintError(ValueError):
    """Raised when a screenshot freshness contract cannot be fingerprinted."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _canonical_source_bytes(path: Path, content: bytes) -> bytes:
    """Make text fingerprints independent of checkout newline settings."""

    if path.suffix.casefold() not in TEXT_SOURCE_SUFFIXES:
        return content
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _safe_repo_path(repo_root: Path, value: str) -> Path:
    if not value or "\\" in value or ":" in value:
        raise CaptureFingerprintError(f"unsafe repository path: {value!r}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise CaptureFingerprintError(f"unsafe repository path: {value!r}")
    resolved_root = repo_root.resolve()
    resolved = (resolved_root / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise CaptureFingerprintError(
            f"repository path escapes the project: {value!r}"
        ) from exc
    return resolved


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaptureFingerprintError(f"{label} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise CaptureFingerprintError(f"{label} must be a non-empty string array")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_required_string(item, f"{label}[{index}]"))
    if len(result) != len(set(result)):
        raise CaptureFingerprintError(f"{label} contains duplicate values")
    return result


def _viewport(value: Any) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        or any(item <= 0 for item in value)
    ):
        raise CaptureFingerprintError("viewport must contain two positive integers")
    return [value[0], value[1]]


def _handler_metadata(repo_root: Path, handler: str) -> dict[str, Any]:
    handler_source = _safe_repo_path(repo_root, DEFAULT_HANDLER_SOURCE)
    try:
        source = handler_source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CaptureFingerprintError(
            f"cannot read capture handler source {DEFAULT_HANDLER_SOURCE}: {exc}"
        ) from exc
    try:
        tree = ast.parse(source, filename=DEFAULT_HANDLER_SOURCE)
    except SyntaxError as exc:
        raise CaptureFingerprintError(
            f"cannot parse capture handler source {DEFAULT_HANDLER_SOURCE}: {exc}"
        ) from exc

    class_node = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == DEFAULT_HANDLER_CLASS
        ),
        None,
    )
    if class_node is None:
        raise CaptureFingerprintError(
            f"capture handler class is missing: {DEFAULT_HANDLER_CLASS}"
        )
    methods = {
        node.name: node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    handler_method = f"_capture_handler_{handler}"
    method_names = (*CAPTURE_PIPELINE_METHODS, handler_method)
    source_lines = source.splitlines(keepends=True)
    method_entries: list[dict[str, str]] = []
    for method_name in method_names:
        node = methods.get(method_name)
        if node is None or node.end_lineno is None:
            raise CaptureFingerprintError(
                f"capture handler method is missing: "
                f"{DEFAULT_HANDLER_CLASS}.{method_name}"
            )
        start_line = min(
            [node.lineno]
            + [decorator.lineno for decorator in node.decorator_list]
        )
        snippet = "".join(source_lines[start_line - 1 : node.end_lineno])
        method_entries.append(
            {
                "name": method_name,
                "sha256": _sha256_bytes(snippet.encode("utf-8")),
            }
        )
    return {
        "name": handler,
        "source": DEFAULT_HANDLER_SOURCE,
        "class": DEFAULT_HANDLER_CLASS,
        "methods": method_entries,
    }


def _expand_source_paths(repo_root: Path, patterns: list[str]) -> list[Path]:
    resolved_root = repo_root.resolve()
    files: dict[str, Path] = {}
    for pattern in patterns:
        wildcard_indexes = [
            index
            for token in "*?["
            if (index := pattern.find(token)) >= 0
        ]
        fixed_prefix = pattern[
            : min(wildcard_indexes) if wildcard_indexes else len(pattern)
        ].rstrip("/")
        if not fixed_prefix:
            raise CaptureFingerprintError(
                f"source path pattern needs a fixed repository prefix: {pattern!r}"
            )
        _safe_repo_path(resolved_root, fixed_prefix)
        try:
            matches = list(resolved_root.glob(pattern)) if any(
                token in pattern for token in "*?["
            ) else [_safe_repo_path(resolved_root, pattern)]
        except (OSError, ValueError) as exc:
            raise CaptureFingerprintError(
                f"cannot expand source path {pattern!r}: {exc}"
            ) from exc
        expanded: list[Path] = []
        for match in matches:
            resolved = match.resolve()
            try:
                resolved.relative_to(resolved_root)
            except ValueError as exc:
                raise CaptureFingerprintError(
                    f"source path escapes the project: {pattern!r}"
                ) from exc
            if resolved.is_dir():
                expanded.extend(path for path in resolved.rglob("*") if path.is_file())
            elif resolved.is_file():
                expanded.append(resolved)
        if not expanded:
            raise CaptureFingerprintError(
                f"source path does not match any file: {pattern!r}"
            )
        for path in expanded:
            relative = path.resolve().relative_to(resolved_root).as_posix()
            files[relative] = path.resolve()
    return [files[key] for key in sorted(files)]


def _source_metadata(
    repo_root: Path,
    source_paths: list[str],
) -> list[dict[str, Any]]:
    resolved_root = repo_root.resolve()
    entries: list[dict[str, Any]] = []
    for path in _expand_source_paths(resolved_root, source_paths):
        try:
            content = _canonical_source_bytes(path, path.read_bytes())
        except OSError as exc:
            raise CaptureFingerprintError(f"cannot read screenshot source {path}: {exc}") from exc
        entries.append(
            {
                "path": path.relative_to(resolved_root).as_posix(),
                "size": len(content),
                "sha256": _sha256_bytes(content),
            }
        )
    return entries


def build_capture_fingerprint(
    repo_root: Path,
    config: Mapping[str, Any],
    scenario: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the JSON-serializable fingerprint stored beside a guide PNG."""

    scenario_id = _required_string(scenario.get("id"), "scenario.id")
    guide_asset = _required_string(scenario.get("guide_asset"), "scenario.guide_asset")
    asset_path = Path(guide_asset)
    if (
        asset_path.is_absolute()
        or asset_path.name != guide_asset
        or asset_path.suffix.casefold() != ".png"
    ):
        raise CaptureFingerprintError(
            f"scenario {scenario_id}: guide_asset must be a folder-free PNG name"
        )
    handler = _required_string(
        scenario.get("capture_handler"),
        f"scenario {scenario_id}.capture_handler",
    )
    if not HANDLER_RE.fullmatch(handler):
        raise CaptureFingerprintError(
            f"scenario {scenario_id}: invalid capture_handler {handler!r}"
        )
    viewport = _viewport(scenario.get("viewport"))
    object_names = _string_list(
        scenario.get("expected_object_names"),
        f"scenario {scenario_id}.expected_object_names",
    )
    invalid_object_names = [
        name for name in object_names if not OBJECT_NAME_RE.fullmatch(name)
    ]
    if invalid_object_names:
        raise CaptureFingerprintError(
            f"scenario {scenario_id}: invalid expected objectName values: "
            f"{invalid_object_names}"
        )
    source_paths = _string_list(
        scenario.get("source_paths"),
        f"scenario {scenario_id}.source_paths",
    )
    handler_metadata = _handler_metadata(repo_root, handler)
    source_files = _source_metadata(repo_root, source_paths)
    contract = {
        "config": {
            "schema_version": config.get("schema_version"),
            "application": config.get("application"),
            "capture_delay_ms": config.get("capture_delay_ms"),
        },
        "scenario": dict(scenario),
    }
    contract_sha256 = _sha256_json(contract)
    handler_sha256 = _sha256_json(handler_metadata)
    sources_sha256 = _sha256_json(
        {"source_paths": source_paths, "source_files": source_files}
    )
    basis = {
        "schema_version": CAPTURE_FINGERPRINT_SCHEMA_VERSION,
        "contract_sha256": contract_sha256,
        "handler_sha256": handler_sha256,
        "sources_sha256": sources_sha256,
    }
    return {
        "schema_version": CAPTURE_FINGERPRINT_SCHEMA_VERSION,
        "algorithm": "sha256",
        "value": _sha256_json(basis),
        "contract_sha256": contract_sha256,
        "handler_sha256": handler_sha256,
        "sources_sha256": sources_sha256,
        "capture_handler": handler_metadata,
        "viewport": viewport,
        "expected_object_names": object_names,
        "source_paths": source_paths,
        "source_files": source_files,
    }


def expected_capture_fingerprints(
    repo_root: Path,
    config: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return current fingerprints for every guide_asset scenario in a config."""

    scenarios = config.get("scenarios")
    if not isinstance(scenarios, list):
        raise CaptureFingerprintError("capture config scenarios must be an array")
    fingerprints: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(scenarios):
        if not isinstance(value, Mapping):
            raise CaptureFingerprintError(f"scenarios[{index}] must be an object")
        if not str(value.get("guide_asset", "")).strip():
            continue
        scenario_id = _required_string(value.get("id"), f"scenarios[{index}].id")
        if scenario_id in fingerprints:
            raise CaptureFingerprintError(f"duplicate guide scenario ID: {scenario_id}")
        fingerprints[scenario_id] = build_capture_fingerprint(
            repo_root,
            config,
            value,
        )
    return fingerprints


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptureFingerprintError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CaptureFingerprintError(f"{label} root must be an object: {path}")
    return value


def _png_dimensions(path: Path) -> tuple[int, int]:
    try:
        header = path.read_bytes()[:24]
    except OSError as exc:
        raise CaptureFingerprintError(f"cannot read guide PNG {path}: {exc}") from exc
    if len(header) < 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise CaptureFingerprintError(f"invalid guide PNG header: {path}")
    return struct.unpack(">II", header[16:24])


def capture_manifest_errors(
    repo_root: Path,
    scenario_config_path: Path,
    capture_manifest_path: Path,
    *,
    assets_root: Path | None = None,
) -> list[str]:
    """Compare a capture manifest and its PNGs with the current source state.

    This function is validator-ready: it never raises for malformed/stale
    capture state and instead returns user-facing error strings.
    """

    try:
        config = _load_json_object(scenario_config_path, "capture scenario config")
        manifest = _load_json_object(capture_manifest_path, "capture manifest")
        expected = expected_capture_fingerprints(repo_root, config)
    except CaptureFingerprintError as exc:
        return [str(exc)]

    errors: list[str] = []
    if manifest.get("schema_version") != CAPTURE_MANIFEST_SCHEMA_VERSION:
        errors.append(
            "capture manifest schema_version must be "
            f"{CAPTURE_MANIFEST_SCHEMA_VERSION}"
        )
    if manifest.get("fingerprint_algorithm") != "sha256":
        errors.append("capture manifest fingerprint_algorithm must be 'sha256'")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        return errors + ["capture manifest assets must be an array"]
    records: dict[str, Mapping[str, Any]] = {}
    for index, value in enumerate(assets):
        if not isinstance(value, Mapping):
            errors.append(f"capture manifest assets[{index}] must be an object")
            continue
        scenario_id = value.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            errors.append(f"capture manifest assets[{index}].scenario_id is invalid")
            continue
        if scenario_id in records:
            errors.append(f"duplicate capture manifest scenario: {scenario_id}")
            continue
        records[scenario_id] = value

    scenario_values = {
        str(item.get("id")): item
        for item in config.get("scenarios", [])
        if isinstance(item, Mapping) and str(item.get("guide_asset", "")).strip()
    }
    output_root = (assets_root or capture_manifest_path.parent).resolve()
    for scenario_id, fingerprint in expected.items():
        scenario = scenario_values[scenario_id]
        record = records.get(scenario_id)
        if record is None:
            errors.append(f"missing capture manifest scenario: {scenario_id}")
            continue
        expected_file = str(scenario["guide_asset"])
        if record.get("file") != expected_file:
            errors.append(
                f"capture {scenario_id}: file must be {expected_file!r}"
            )
        if record.get("viewport") != fingerprint["viewport"]:
            errors.append(f"capture {scenario_id}: recorded viewport is stale")
        if record.get("expected_object_names") != fingerprint["expected_object_names"]:
            errors.append(
                f"capture {scenario_id}: recorded objectName expectations are stale"
            )
        if record.get("capture_handler") != str(scenario["capture_handler"]):
            errors.append(f"capture {scenario_id}: recorded capture handler is stale")
        if record.get("source_paths") != fingerprint["source_paths"]:
            errors.append(f"capture {scenario_id}: recorded source paths are stale")

        recorded_fingerprint = record.get("fingerprint")
        if not isinstance(recorded_fingerprint, Mapping):
            errors.append(f"capture {scenario_id}: fingerprint is missing")
        elif recorded_fingerprint.get("value") != fingerprint["value"]:
            stale_components: list[str] = []
            for key, label in (
                ("contract_sha256", "scenario contract"),
                ("handler_sha256", "capture handler"),
                ("sources_sha256", "mapped sources"),
            ):
                if recorded_fingerprint.get(key) != fingerprint[key]:
                    stale_components.append(label)
            suffix = (
                f" ({', '.join(stale_components)})"
                if stale_components
                else ""
            )
            errors.append(f"capture {scenario_id}: fingerprint is stale{suffix}")
        elif dict(recorded_fingerprint) != fingerprint:
            errors.append(f"capture {scenario_id}: fingerprint metadata is stale")

        relative_file = Path(expected_file)
        if relative_file.name != expected_file:
            errors.append(f"capture {scenario_id}: unsafe PNG filename {expected_file!r}")
            continue
        image_path = (output_root / relative_file).resolve()
        try:
            image_path.relative_to(output_root)
        except ValueError:
            errors.append(f"capture {scenario_id}: PNG path escapes assets root")
            continue
        if not image_path.is_file():
            errors.append(f"capture {scenario_id}: PNG is missing: {expected_file}")
            continue
        try:
            image_sha256 = _sha256_bytes(image_path.read_bytes())
        except OSError as exc:
            errors.append(f"cannot read guide PNG {image_path}: {exc}")
            continue
        if record.get("png_sha256") != image_sha256:
            errors.append(f"capture {scenario_id}: PNG content hash is stale")
        try:
            dimensions = _png_dimensions(image_path)
        except CaptureFingerprintError as exc:
            errors.append(str(exc))
            continue
        expected_dimensions = tuple(fingerprint["viewport"])
        if dimensions != expected_dimensions:
            errors.append(
                f"capture {scenario_id}: PNG dimensions {dimensions[0]}x{dimensions[1]} "
                f"do not match viewport {expected_dimensions[0]}x{expected_dimensions[1]}"
            )

    for scenario_id in sorted(set(records) - set(expected)):
        errors.append(f"orphan capture manifest scenario: {scenario_id}")
    return errors


__all__ = [
    "CAPTURE_FINGERPRINT_SCHEMA_VERSION",
    "CAPTURE_MANIFEST_SCHEMA_VERSION",
    "CaptureFingerprintError",
    "build_capture_fingerprint",
    "capture_manifest_errors",
    "expected_capture_fingerprints",
]
