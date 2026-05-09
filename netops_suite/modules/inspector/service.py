from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml


InspectorMode = Literal["inspection", "backup", "inspection_backup", "custom_commands"]


@dataclass(slots=True)
class InspectorRunRequest:
    inventory_path: str
    mode: InspectorMode = "inspection"
    inventory_password: str | None = None
    command_path: str | None = None
    commands: list[str] | None = None
    output_name: str = "inspection_results.xlsx"
    max_retries: int = 3
    timeout: int = 10
    max_workers: int = 10


@dataclass(slots=True)
class InspectorRunResult:
    mode: InspectorMode
    devices_total: int
    results_total: int
    result_excel: str | None
    backup_dir: str | None
    session_log_dir: str | None
    results: list[dict[str, Any]]


class InspectorService:
    """GUI-friendly wrapper around the migrated netops-inspector runtime."""

    def __init__(
        self,
        runtime_dir: str | Path | None = None,
        work_dir: str | Path | None = None,
        user_data_dir: str | Path | None = None,
    ) -> None:
        package_root = Path(__file__).resolve().parents[1]
        self.runtime_dir = Path(runtime_dir) if runtime_dir else package_root / "inspector_runtime"
        self.work_dir = Path(work_dir) if work_dir else Path.cwd()
        self.user_data_dir = Path(user_data_dir) if user_data_dir else self.work_dir
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.custom_parsers_dir.mkdir(parents=True, exist_ok=True)
        self.vendor_templates_dir = package_root / "inspector" / "vendor_templates"

    @property
    def custom_rules_path(self) -> Path:
        return self.user_data_dir / "custom_rules.yaml"

    @property
    def custom_parsers_dir(self) -> Path:
        return self.user_data_dir / "custom_parsers"

    def supported_profiles(self) -> dict[str, list[str]]:
        with self._runtime_import_path():
            from vendors import INSPECTION_COMMANDS

            return {vendor: sorted(os_map.keys()) for vendor, os_map in sorted(INSPECTION_COMMANDS.items())}

    def supported_profile_templates(self) -> list[dict[str, Any]]:
        with self._runtime_import_path():
            from vendors import (
                BACKUP_COMMANDS,
                CONNECTION_OVERRIDES,
                CUSTOM_PARSERS,
                HANDLER_OVERRIDES,
                INSPECTION_COMMANDS,
                PARSING_RULES,
                is_custom_rule_pair,
            )

            templates: list[dict[str, Any]] = []
            for vendor, os_map in sorted(INSPECTION_COMMANDS.items()):
                for os_name, commands in sorted(os_map.items()):
                    command_list = list(commands or [])
                    parse_rules = PARSING_RULES.get(vendor, {}).get(os_name, {})
                    output_columns = self._collect_output_columns(parse_rules)
                    backup_command = BACKUP_COMMANDS.get(vendor, {}).get(os_name, "")
                    connection = CONNECTION_OVERRIDES.get(vendor, {}).get(os_name, {})
                    handler = HANDLER_OVERRIDES.get(vendor, {}).get(os_name, {})
                    templates.append(
                        {
                            "vendor": vendor,
                            "model": "",
                            "os": os_name,
                            "os_version": "",
                            "key": f"{vendor}|{os_name}",
                            "command_count": len(command_list),
                            "commands": command_list,
                            "backup_command": backup_command,
                            "has_backup": bool(backup_command),
                            "parse_rule_count": len(parse_rules) if isinstance(parse_rules, dict) else 0,
                            "parsing_rules": parse_rules if isinstance(parse_rules, dict) else {},
                            "output_columns": output_columns,
                            "connection_overrides": dict(connection) if isinstance(connection, dict) else {},
                            "handler_overrides": dict(handler) if isinstance(handler, dict) else {},
                            "custom_parsers": sorted(CUSTOM_PARSERS.keys()),
                            "is_custom": is_custom_rule_pair(vendor, os_name),
                        }
                    )
            return templates

    def build_vendor_template_yaml(self, vendor: str, os_name: str) -> str:
        vendor_key = self._normalize_key(vendor)
        os_key = self._normalize_key(os_name)
        if not vendor_key or not os_key:
            raise ValueError("벤더와 OS 이름이 필요합니다.")

        with self._runtime_import_path():
            from vendors import BACKUP_COMMANDS, CONNECTION_OVERRIDES, HANDLER_OVERRIDES, INSPECTION_COMMANDS, PARSING_RULES

            document = {
                "inspection_commands": {vendor_key: {os_key: list(INSPECTION_COMMANDS.get(vendor_key, {}).get(os_key, []))}},
                "backup_commands": {vendor_key: {os_key: BACKUP_COMMANDS.get(vendor_key, {}).get(os_key, "")}},
                "parsing_rules": {vendor_key: {os_key: PARSING_RULES.get(vendor_key, {}).get(os_key, {})}},
                "connection_overrides": {vendor_key: {os_key: CONNECTION_OVERRIDES.get(vendor_key, {}).get(os_key, {})}},
                "handler_overrides": {vendor_key: {os_key: HANDLER_OVERRIDES.get(vendor_key, {}).get(os_key, {})}},
            }
        return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)

    def ensure_vendor_template_files(self) -> int:
        self.vendor_templates_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for template in self.supported_profile_templates():
            vendor = template["vendor"]
            os_name = template["os"]
            path = self.vendor_templates_dir / f"{self._safe_file_part(vendor)}__{self._safe_file_part(os_name)}.yaml"
            path.write_text(self.build_vendor_template_yaml(vendor, os_name), encoding="utf-8")
            count += 1
        return count

    def load_custom_rules_text(self) -> str:
        if self.custom_rules_path.exists():
            return self.custom_rules_path.read_text(encoding="utf-8")
        example = self.runtime_dir / "custom_rules.example.yaml"
        if example.exists():
            return example.read_text(encoding="utf-8")
        return ""

    def save_custom_rules_text(self, text: str) -> Path:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("custom_rules YAML 최상위 구조는 object여야 합니다.")
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.custom_rules_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        self.reload_runtime_modules()
        return self.custom_rules_path

    def build_simple_custom_rules_yaml(
        self,
        *,
        vendor: str,
        os_name: str,
        inspection_commands: list[str],
        backup_command: str = "",
        default_device_type: str = "",
        telnet_device_type: str = "",
        parser_rows: list[dict[str, Any]] | None = None,
        handler_overrides: dict[str, Any] | None = None,
        model: str = "",
        os_version: str = "",
        output_columns: list[str] | None = None,
        parsing_rules: dict[str, Any] | None = None,
    ) -> str:
        vendor_key = self._normalize_key(vendor)
        os_key = self._normalize_key(os_name)
        if not vendor_key or not os_key:
            raise ValueError("벤더와 OS 이름이 필요합니다.")
        cleaned_commands = [command.strip() for command in inspection_commands if command.strip()]
        if not cleaned_commands:
            raise ValueError("점검 명령을 하나 이상 입력하세요.")

        document: dict[str, Any] = {"inspection_commands": {vendor_key: {os_key: cleaned_commands}}}
        if backup_command.strip():
            document["backup_commands"] = {vendor_key: {os_key: backup_command.strip()}}

        connection_override: dict[str, str] = {}
        if default_device_type.strip():
            connection_override["default"] = default_device_type.strip()
            connection_override["ssh"] = default_device_type.strip()
        if telnet_device_type.strip():
            connection_override["telnet"] = telnet_device_type.strip()
        if connection_override:
            document["connection_overrides"] = {vendor_key: {os_key: connection_override}}

        rule_map = parsing_rules if isinstance(parsing_rules, dict) else self._build_parsing_rules_from_rows(parser_rows or [])
        if rule_map:
            document["parsing_rules"] = {vendor_key: {os_key: rule_map}}

        cleaned_handler = {key: value for key, value in (handler_overrides or {}).items() if value not in ("", None)}
        if cleaned_handler:
            document["handler_overrides"] = {vendor_key: {os_key: cleaned_handler}}

        metadata = {key: value for key, value in {"model": model.strip(), "os_version": os_version.strip()}.items() if value}
        cleaned_columns = [str(column).strip() for column in (output_columns or []) if str(column).strip()]
        if cleaned_columns:
            metadata["output_columns"] = cleaned_columns
        if metadata:
            document["profile_metadata"] = {vendor_key: {os_key: metadata}}

        return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)

    def reload_runtime_modules(self) -> None:
        for module_name in list(sys.modules):
            if module_name == "vendors" or module_name.startswith("vendors.") or module_name in {
                "core.inspector",
                "core.validator",
                "core.file_handler",
                "core.settings",
                "core.path_utils",
            }:
                sys.modules.pop(module_name, None)

    def load_inventory(self, path: str, password: str | None = None) -> list[dict[str, Any]]:
        with self._runtime_import_path():
            from core.file_handler import read_excel_file
            from core.validator import validate_dataframe

            df = read_excel_file(path, password)
            validated = validate_dataframe(df)
            return validated.to_dict("records")

    def read_command_file(self, path: str) -> list[str]:
        with self._runtime_import_path():
            from core.file_handler import read_command_file

            return read_command_file(path)

    def run(self, request: InspectorRunRequest, progress_callback: Any | None = None) -> InspectorRunResult:
        def emit(event: dict[str, Any]) -> None:
            if progress_callback is None:
                return
            if hasattr(progress_callback, "emit"):
                progress_callback.emit(event)
            else:
                progress_callback(event)

        run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with self._runtime_import_path(), self._working_directory():
            from core.file_handler import save_results_to_excel
            from core.inspector import NetworkInspector
            from core.settings import load_settings, resolve_inspection_column_order

            emit({"type": "running", "message": "인벤토리를 불러오는 중입니다."})
            settings = load_settings()
            devices = self.load_inventory(request.inventory_path, request.inventory_password)
            for device in devices:
                device.setdefault("username", "")
                device.setdefault("enable_password", "")

            commands = list(request.commands or [])
            if request.command_path:
                commands = self.read_command_file(request.command_path)

            inspector = NetworkInspector(
                request.output_name,
                backup_only=request.mode == "backup",
                inspection_only=request.mode == "inspection",
                run_timestamp=run_stamp,
                inspection_excludes=settings.inspection_excludes,
                max_retries=request.max_retries or settings.max_retries,
                timeout=request.timeout or settings.timeout,
                max_workers=request.max_workers or settings.max_workers,
                column_aliases=settings.column_aliases,
                status_callback=emit,
            )
            inspector.load_devices(devices)
            emit({"type": "progress", "message": f"장비 {len(devices)}대를 실행 대기열에 올렸습니다."})

            column_order: list[str] | None = None
            if request.mode in {"inspection", "inspection_backup"}:
                column_order = resolve_inspection_column_order(
                    inspector.get_available_inspection_columns(devices),
                    inspector.get_device_profile_keys(devices),
                    settings,
                )

            if request.mode == "backup":
                inspector.inspect_devices(backup_only=True)
                result_excel = inspector.output_excel.replace("inspection_results", "backup_summary")
                save_results_to_excel(inspector.results, result_excel, column_order=column_order, column_aliases=settings.column_aliases)
            elif request.mode == "inspection_backup":
                inspector.inspect_and_backup_devices()
                result_excel = inspector.output_excel
                save_results_to_excel(inspector.results, result_excel, column_order=column_order, column_aliases=settings.column_aliases)
            elif request.mode == "custom_commands":
                if not commands:
                    raise ValueError("사용자 명령 파일 또는 명령 목록이 필요합니다.")
                inspector.run_custom_commands(commands)
                result_excel = inspector.output_excel.replace("inspection_results", "command_results")
                save_results_to_excel(inspector.results, result_excel, column_aliases=settings.column_aliases)
            else:
                inspector.inspect_devices(backup_only=False)
                result_excel = inspector.output_excel
                save_results_to_excel(inspector.results, result_excel, column_order=column_order, column_aliases=settings.column_aliases)

            emit({"type": "done", "message": "장비 점검 작업이 완료되었습니다."})
            return InspectorRunResult(
                mode=request.mode,
                devices_total=len(devices),
                results_total=len(inspector.results),
                result_excel=str(Path(result_excel).resolve()) if result_excel else None,
                backup_dir=str(Path(inspector.backup_dir).resolve()) if Path(inspector.backup_dir).exists() else None,
                session_log_dir=str(Path(inspector.session_log_dir).resolve()) if Path(inspector.session_log_dir).exists() else None,
                results=list(inspector.results),
            )

    @contextlib.contextmanager
    def _runtime_import_path(self):
        runtime_path = str(self.runtime_dir)
        previous_data_dir = os.environ.get("NETOPS_SUITE_INSPECTOR_DATA_DIR")
        os.environ["NETOPS_SUITE_INSPECTOR_DATA_DIR"] = str(self.user_data_dir)
        inserted = False
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)
            inserted = True
        try:
            yield
        finally:
            if previous_data_dir is None:
                os.environ.pop("NETOPS_SUITE_INSPECTOR_DATA_DIR", None)
            else:
                os.environ["NETOPS_SUITE_INSPECTOR_DATA_DIR"] = previous_data_dir
            if inserted:
                with contextlib.suppress(ValueError):
                    sys.path.remove(runtime_path)

    @staticmethod
    def _normalize_key(value: str) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _safe_file_part(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip().lower()) or "profile"

    @staticmethod
    def _collect_output_columns(parse_rules: object) -> list[str]:
        columns: list[str] = []

        def add(column: object) -> None:
            text = str(column or "").strip()
            if text and text not in columns:
                columns.append(text)

        if not isinstance(parse_rules, dict):
            return columns
        for rules in parse_rules.values():
            if not isinstance(rules, dict):
                continue
            add(rules.get("output_column"))
            output_columns = rules.get("output_columns", [])
            if isinstance(output_columns, list):
                for column in output_columns:
                    add(column)
            pattern_rules = rules.get("patterns", [])
            if isinstance(pattern_rules, list):
                for pattern_rule in pattern_rules:
                    if not isinstance(pattern_rule, dict):
                        continue
                    add(pattern_rule.get("output_column"))
                    nested_columns = pattern_rule.get("output_columns", [])
                    if isinstance(nested_columns, list):
                        for column in nested_columns:
                            add(column)
                    process = pattern_rule.get("process", {})
                    if isinstance(process, dict):
                        add(process.get("output_column"))
            process = rules.get("process", {})
            if isinstance(process, dict):
                add(process.get("output_column"))
        return columns

    def available_custom_parsers(self) -> list[str]:
        with self._runtime_import_path():
            from vendors import CUSTOM_PARSERS

            return sorted(CUSTOM_PARSERS.keys())

    def discover_user_custom_parsers(self) -> list[str]:
        parsers: set[str] = set()
        for path in sorted(self.custom_parsers_dir.glob("*.py")):
            spec = importlib.util.spec_from_file_location(f"netops_user_parser_{path.stem}", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:
                continue
            for name in dir(module):
                if name.startswith("parsing_") and callable(getattr(module, name)):
                    parsers.add(name)
        return sorted(parsers)

    def save_custom_parser(self, function_name: str, code: str) -> Path:
        function_name = self._normalize_parser_function_name(function_name)
        self._load_custom_parser_function(function_name, code)
        path = self.custom_parsers_dir / f"{function_name}.py"
        path.write_text(code.rstrip() + "\n", encoding="utf-8")
        self.reload_runtime_modules()
        return path

    def test_custom_parser_code(self, function_name: str, code: str, sample_output: str) -> Any:
        function_name = self._normalize_parser_function_name(function_name)
        parser = self._load_custom_parser_function(function_name, code)
        return parser(sample_output)

    def _load_custom_parser_function(self, function_name: str, code: str):
        namespace: dict[str, Any] = {}
        exec(compile(code, f"<{function_name}>", "exec"), namespace)
        parser = namespace.get(function_name)
        if not callable(parser):
            raise ValueError(f"{function_name} 함수를 찾을 수 없습니다.")
        return parser

    @staticmethod
    def _normalize_parser_function_name(function_name: str) -> str:
        name = str(function_name or "").strip()
        if not name:
            raise ValueError("함수 이름을 입력하세요. 예: parsing_cpu_usage")
        if not name.startswith("parsing_"):
            name = f"parsing_{name}"
        if not re.fullmatch(r"parsing_[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("함수 이름은 parsing_으로 시작하고 영문/숫자/밑줄만 사용할 수 있습니다.")
        return name

    @staticmethod
    def _build_parsing_rules_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        parsing_rules: dict[str, dict[str, Any]] = {}
        for row in rows:
            command = str(row.get("command", "")).strip()
            column = str(row.get("output_column", "")).strip()
            pattern = str(row.get("pattern", "")).strip()
            patterns_text = str(row.get("patterns", "")).strip()
            custom_parser = str(row.get("custom_parser", "")).strip()
            parser_type = str(row.get("parser_type", "")).strip()
            process_text = str(row.get("process", "")).strip()
            first_match_text = str(row.get("first_match_only", "true")).strip().lower()
            if not command:
                continue
            rule: dict[str, Any] = {}
            if parser_type in {"split_fields", "keyword_after", "line_text"}:
                if not column:
                    continue
                rule["parser_type"] = parser_type
                rule["output_column"] = column
                if parser_type in {"split_fields", "line_text"}:
                    rule["line_number"] = int(row.get("line_number", 1) or 1)
                if parser_type == "split_fields":
                    rule["start_field"] = int(row.get("start_field", 1) or 1)
                    rule["end_field"] = int(row.get("end_field", row.get("start_field", 1)) or row.get("start_field", 1) or 1)
                    rule["delimiter"] = str(row.get("delimiter", "whitespace") or "whitespace")
                if parser_type == "keyword_after":
                    rule["keyword"] = str(row.get("keyword", "")).strip()
                    if not rule["keyword"]:
                        continue
            elif custom_parser:
                rule["custom_parser"] = custom_parser
                if column:
                    rule["output_column"] = column
            elif patterns_text:
                loaded_patterns = yaml.safe_load(patterns_text)
                if not isinstance(loaded_patterns, list):
                    raise ValueError(f"patterns는 YAML list여야 합니다: {command}")
                rule["patterns"] = loaded_patterns
            elif pattern and column:
                rule["pattern"] = pattern
                rule["output_column"] = column
                rule["first_match_only"] = first_match_text not in {"false", "0", "no", "n"}
            else:
                continue
            if process_text:
                loaded_process = yaml.safe_load(process_text)
                if not isinstance(loaded_process, dict):
                    raise ValueError(f"process는 YAML object여야 합니다: {command}")
                rule["process"] = loaded_process
            if command in parsing_rules:
                existing = parsing_rules[command]
                if "patterns" in existing and isinstance(existing["patterns"], list):
                    existing["patterns"].append(rule)
                else:
                    parsing_rules[command] = {"patterns": [existing, rule]}
            else:
                parsing_rules[command] = rule
        return parsing_rules

    @contextlib.contextmanager
    def _working_directory(self):
        previous = Path.cwd()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(self.work_dir)
        try:
            yield
        finally:
            os.chdir(previous)
