import logging
import os
import msvcrt

from InquirerPy import inquirer
from InquirerPy.separator import Separator
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.settings import save_settings, AppSettings
from vendors import INSPECTION_COMMANDS, PARSING_RULES

logger = logging.getLogger(__name__)
console = Console()

BANNER_TEXT = r"""
⡴⠒⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣼⠉⠳⡆⠀
⣇⠰⠉⢙⡄⠀⠀⣴⠖⢦⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣆⠁⠙⡆
⠘⡇⢠⠞⠉⠙⣾⠃⢀⡼⠀⠀⠀⠀⠀⠀⠀⢀⣼⡀⠄⢷⣄⣀⠀⠀⠀⠀⠀⠀⠀⠰⠒⠲⡄⠀⣏⣆⣀⡍
⠀⢠⡏⠀⡤⠒⠃⠀⡜⠀⠀⠀⠀⠀⢀⣴⠾⠛⡁⠀⠀⢀⣈⡉⠙⠳⣤⡀⠀⠀⠀⠘⣆⠀⣇⡼⢋⠀⠀⢱
⠀⠘⣇⠀⠀⠀⠀⠀⡇⠀⠀⠀⠀⡴⢋⡣⠊⡩⠋⠀⠀⠀⠣⡉⠲⣄⠀⠙⢆⠀⠀⠀⣸⠀⢉⠀⢀⠿⠀⢸
⠀⠀⠸⡄⠀⠈⢳⣄⡇⠀⠀⢀⡞⠀⠈⠀⢀⣴⣾⣿⣿⣿⣿⣦⡀⠀⠀⠀⠈⢧⠀⠀⢳⣰⠁⠀⠀⠀⣠⠃
⠀⠀⠀⠘⢄⣀⣸⠃⠀⠀⠀⡸⠀⠀⠀⢠⣿⣿⣿⣿⣿⣿⣿⣿⣿⣆⠀⠀⠀⠈⣇⠀⠀⠙⢄⣀⠤⠚⠁⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡇⠀⠀⢠⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡄⠀⠀⠀⢹⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⠀⠀⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡀⠀⠀⢘⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡇⠀⢰⣿⣿⣿⡿⠛⠁⠀⠉⠛⢿⣿⣿⣿⣧⠀⠀⣼⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⡀⣸⣿⣿⠟⠀⠀⠀⠀⠀⠀⠀⢻⣿⣿⣿⡀⢀⠇⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⡇⠹⠿⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢿⡿⠁⡏⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠻⣤⣞⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢢⣀⣠⠇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠲⢤⣀⣀⠀⢀⣀⣀⠤⠒⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
""".strip()


def _clear() -> None:
    console.clear()


def _show_banner() -> None:
    banner = Text(BANNER_TEXT, justify="center")
    panel = Panel(
        banner,
        title="[bold cyan]네트워크 장비 점검 프로그램[/bold cyan]",
        border_style="cyan",
        expand=False,
    )
    console.print(panel)
    console.print()


# ---------------------------------------------------------------------------
# 메인 / 작업 선택 메뉴
# ---------------------------------------------------------------------------

def show_main_menu() -> str:
    """프로그램 시작 메뉴"""
    _clear()
    _show_banner()

    choices = [
        {"name": "작업 시작 (점검/백업 선택)", "value": "1"},
        {"name": "사용자 명령 파일 실행", "value": "2"},
        {"name": "설정 변경", "value": "3"},
        {"name": "Netmiko device_type 목록 보기", "value": "4"},
        Separator(),
        {"name": "종료", "value": "5"},
    ]
    return inquirer.select(
        message="원하는 작업을 선택하세요:",
        choices=choices,
        default="1",
        pointer="▶",
    ).execute()


def show_netmiko_device_types() -> None:
    """Netmiko가 지원하는 device_type 목록을 fuzzy 검색으로 표시합니다."""
    try:
        from netmiko.ssh_dispatcher import CLASS_MAPPER
        device_types = sorted(CLASS_MAPPER.keys())
    except Exception as e:
        console.print(f"[red]device_type 목록을 가져오는 데 실패했습니다: {e}[/red]")
        input("Enter를 누르면 돌아갑니다.")
        return

    if not device_types:
        console.print("[yellow]표시할 device_type이 없습니다.[/yellow]")
        input("Enter를 누르면 돌아갑니다.")
        return

    _clear()
    result = inquirer.fuzzy(
        message="device_type 검색 (ESC=뒤로가기):",
        choices=device_types,
        pointer="▶",
        info=True,
        mandatory=False,
    ).execute()

    if result:
        console.print(f"\n선택된 device_type: [bold green]{result}[/bold green]")
        input("Enter를 누르면 돌아갑니다.")


def show_action_menu() -> str | None:
    """실행 작업 선택 메뉴"""
    _clear()
    console.print(Panel("실행할 작업을 선택하세요.", title="[bold cyan]작업 선택[/bold cyan]", border_style="cyan", expand=False))
    console.print()

    choices = [
        {"name": "점검만 실행 (백업 없음)", "value": "1"},
        {"name": "백업만 실행 (점검 없음)", "value": "2"},
        {"name": "점검 + 백업 (둘 다)", "value": "3"},
        Separator(),
        {"name": "뒤로가기", "value": None},
    ]
    return inquirer.select(
        message="작업:",
        choices=choices,
        default="1",
        pointer="▶",
    ).execute()


# ---------------------------------------------------------------------------
# 설정 메뉴
# ---------------------------------------------------------------------------

def select_console_log_level(current_level: str) -> str:
    """콘솔 로그 레벨 선택"""
    _clear()
    console.print(Panel(
        f"[현재] 콘솔 로그 레벨: [bold]{current_level}[/bold]\n"
        "일반 사용자라면 'WARNING 이상'을 권장합니다.",
        title="[bold cyan]콘솔 로그 레벨 설정[/bold cyan]",
        border_style="cyan",
        expand=False,
    ))
    console.print()

    level_choices = [
        {"name": "CRITICAL 이상", "value": "CRITICAL"},
        {"name": "ERROR 이상", "value": "ERROR"},
        {"name": "WARNING 이상", "value": "WARNING"},
        {"name": "INFO 이상", "value": "INFO"},
        {"name": "DEBUG 이상", "value": "DEBUG"},
        Separator(),
        {"name": "뒤로가기 (변경 없음)", "value": None},
    ]
    result = inquirer.select(
        message="로그 레벨:",
        choices=level_choices,
        default=current_level,
        pointer="▶",
    ).execute()

    return result if result is not None else current_level


def _input_int(prompt: str, current: int, min_val: int, max_val: int) -> int:
    """정수 값을 직접 입력받습니다. 빈 입력이면 현재 값을 유지합니다."""
    while True:
        raw = input(f"{prompt} ({min_val}-{max_val}, 현재: {current}, Enter=유지): ").strip()
        if not raw:
            return current
        try:
            value = int(raw)
        except ValueError:
            console.print("[red]숫자를 입력하세요.[/red]")
            continue
        if value < min_val or value > max_val:
            console.print(f"[red]{min_val}-{max_val} 범위의 값을 입력하세요.[/red]")
            continue
        return value


def select_max_retries(current: int) -> int:
    """최대 재시도 횟수 입력"""
    _clear()
    console.print(Panel(
        f"[현재] 최대 재시도 횟수: [bold]{current}회[/bold]\n"
        "장비 연결 실패 시 재시도할 최대 횟수입니다.",
        title="[bold cyan]최대 재시도 횟수 설정[/bold cyan]",
        border_style="cyan",
        expand=False,
    ))
    console.print()
    return _input_int("재시도 횟수", current, 1, 10)


def select_timeout(current: int) -> int:
    """연결 타임아웃 입력"""
    _clear()
    console.print(Panel(
        f"[현재] 연결 타임아웃: [bold]{current}초[/bold]\n"
        "장비 접속 시 대기할 최대 시간(초)입니다.",
        title="[bold cyan]연결 타임아웃 설정[/bold cyan]",
        border_style="cyan",
        expand=False,
    ))
    console.print()
    return _input_int("타임아웃(초)", current, 1, 300)


def select_max_workers(current: int) -> int:
    """동시 처리 장비 수 입력"""
    _clear()
    console.print(Panel(
        f"[현재] 동시 처리 장비 수: [bold]{current}대[/bold]\n"
        "병렬로 동시에 접속할 장비 수입니다.\n"
        "장비 수가 많을수록 빠르지만 네트워크 부하가 증가합니다.",
        title="[bold cyan]동시 처리 장비 수 설정[/bold cyan]",
        border_style="cyan",
        expand=False,
    ))
    console.print()
    return _input_int("동시 처리 수", current, 1, 50)


def show_settings_menu(settings: AppSettings) -> None:
    """설정 메뉴"""
    while True:
        _clear()
        table = Table(title="현재 설정", border_style="dim", expand=False)
        table.add_column("항목", style="cyan")
        table.add_column("값", style="bold")
        table.add_row("콘솔 로그 레벨", settings.console_log_level)
        table.add_row("최대 재시도 횟수", f"{settings.max_retries}회")
        table.add_row("연결 타임아웃", f"{settings.timeout}초")
        table.add_row("동시 처리 장비 수", f"{settings.max_workers}대")

        exclude_count = sum(
            len(cmds) for os_map in settings.inspection_excludes.values() for cmds in os_map.values()
        )
        table.add_row("점검 제외 항목", f"{exclude_count}개")
        console.print(table)
        console.print()

        choices = [
            {"name": "콘솔 로그 레벨 변경", "value": "log_level"},
            {"name": "최대 재시도 횟수 변경", "value": "retries"},
            {"name": "연결 타임아웃 변경", "value": "timeout"},
            {"name": "동시 처리 장비 수 변경", "value": "workers"},
            {"name": "점검 제외 설정", "value": "excludes"},
            Separator(),
            {"name": "뒤로가기", "value": None},
        ]
        choice = inquirer.select(
            message="설정:",
            choices=choices,
            pointer="▶",
        ).execute()

        if choice is None:
            return
        if choice == "log_level":
            settings.console_log_level = select_console_log_level(settings.console_log_level)
            save_settings(settings)
        elif choice == "retries":
            settings.max_retries = select_max_retries(settings.max_retries)
            save_settings(settings)
        elif choice == "timeout":
            settings.timeout = select_timeout(settings.timeout)
            save_settings(settings)
        elif choice == "workers":
            settings.max_workers = select_max_workers(settings.max_workers)
            save_settings(settings)
        elif choice == "excludes":
            show_inspection_exclude_menu(settings)


# ---------------------------------------------------------------------------
# Y/N 확인
# ---------------------------------------------------------------------------

def ask_yes_no(prompt: str, default: bool = False) -> bool:
    """Y/N 확인 프롬프트"""
    return inquirer.confirm(
        message=prompt,
        default=default,
    ).execute()


# ---------------------------------------------------------------------------
# 열 순서 재정렬 (커스텀 유지 - InquirerPy에 동등 위젯 없음)
# ---------------------------------------------------------------------------

def _print_reorder_frame(
    entries: list[dict],
    selected: int,
    moving: bool,
    breadcrumb: str,
) -> None:
    _clear()
    status = "[yellow]이동 모드[/yellow]: 선택 항목 위치 변경" if moving else "[green]선택 모드[/green]: 항목/메뉴 선택"
    console.print(Panel(
        f"{breadcrumb}\n{status}\n[dim]조작: ↑/↓ 이동, Enter: 선택/모드 전환[/dim]",
        title="[bold cyan]열 순서 편집[/bold cyan]",
        border_style="cyan",
        expand=False,
    ))
    console.print()

    for i, entry in enumerate(entries):
        is_selected = i == selected
        if entry["type"] == "item":
            marker = "★" if moving and is_selected else " "
            label = f"  [{marker}] {entry['label']}"
        else:
            label = f"  {entry['label']}"

        if is_selected:
            console.print(f"[bold green]▶{label}[/bold green]")
        else:
            console.print(f" {label}")


def reorder_columns_interactive(columns: list[str]) -> list[str] | None:
    """CLI에서 점검 항목 순서를 재정렬합니다."""
    if not columns:
        return []

    original_items = list(columns)
    items = list(columns)
    selected = 0
    moving = False

    while True:
        entries = (
            [{"type": "action", "id": "start", "label": "현재 순서로 시작"}]
            + [{"type": "item", "id": f"item:{idx}", "label": item} for idx, item in enumerate(items)]
            + [
                {"type": "action", "id": "reset", "label": "순서 초기화"},
                {"type": "action", "id": "back", "label": "뒤로가기"},
            ]
        )
        _print_reorder_frame(entries, selected, moving, "점검 결과 열 순서를 정합니다.")

        key = msvcrt.getwch()
        if key in ("\r", "\n"):
            entry = entries[selected]
            if entry["type"] == "action":
                if entry["id"] == "start":
                    return items
                if entry["id"] == "reset":
                    items = list(original_items)
                    selected = 0
                    moving = False
                    continue
                if entry["id"] == "back":
                    return None
            else:
                moving = not moving
            continue
        if key in ("\x00", "\xe0"):
            key = msvcrt.getwch()
            if key == "H":
                if moving:
                    item_indices = [i for i, e in enumerate(entries) if e["type"] == "item"]
                    current_pos = item_indices.index(selected)
                    if current_pos > 0:
                        swap_target = item_indices[current_pos - 1]
                        item_idx = selected - 1
                        items[item_idx], items[item_idx - 1] = items[item_idx - 1], items[item_idx]
                        selected = swap_target
                else:
                    selected = (selected - 1) % len(entries)
            elif key == "P":
                if moving:
                    item_indices = [i for i, e in enumerate(entries) if e["type"] == "item"]
                    current_pos = item_indices.index(selected)
                    if current_pos < len(item_indices) - 1:
                        swap_target = item_indices[current_pos + 1]
                        item_idx = selected - 1
                        items[item_idx], items[item_idx + 1] = items[item_idx + 1], items[item_idx]
                        selected = swap_target
                else:
                    selected = (selected + 1) % len(entries)


# ---------------------------------------------------------------------------
# 점검 제외 설정 메뉴
# ---------------------------------------------------------------------------

def _get_excluded_set(settings: AppSettings, vendor: str, os_name: str) -> set[str]:
    vendor_key = vendor.lower()
    os_key = os_name.lower()
    return set(settings.inspection_excludes.get(vendor_key, {}).get(os_key, []))


def _is_parse_excluded(excludes: set[str], command: str, parse_id: str) -> bool:
    return parse_id in excludes or command in excludes


def _toggle_exclude(settings: AppSettings, vendor: str, os_name: str, parse_id: str) -> None:
    vendor_key = vendor.lower()
    os_key = os_name.lower()
    vendor_map = settings.inspection_excludes.setdefault(vendor_key, {})
    cmd_list = vendor_map.setdefault(os_key, [])

    if parse_id in cmd_list:
        cmd_list = [cmd for cmd in cmd_list if cmd != parse_id]
        if cmd_list:
            vendor_map[os_key] = cmd_list
        else:
            vendor_map.pop(os_key, None)
            if not vendor_map:
                settings.inspection_excludes.pop(vendor_key, None)
    else:
        cmd_list.append(parse_id)
        vendor_map[os_key] = cmd_list

    save_settings(settings)


def _build_exclude_list_for_os(vendor: str, os_name: str) -> list[str]:
    items = _collect_parsing_items(vendor, os_name)
    return [item["id"] for item in items]


def _set_excludes_all(settings: AppSettings, exclude_all: bool) -> None:
    if not exclude_all:
        if settings.inspection_excludes:
            settings.inspection_excludes.clear()
            save_settings(settings)
        return

    new_map: dict[str, dict[str, list[str]]] = {}
    for vendor in INSPECTION_COMMANDS.keys():
        vendor_key = vendor.lower()
        vendor_map: dict[str, list[str]] = {}
        for os_name in INSPECTION_COMMANDS.get(vendor, {}).keys():
            os_key = os_name.lower()
            items = _build_exclude_list_for_os(vendor, os_name)
            if items:
                vendor_map[os_key] = items
        if vendor_map:
            new_map[vendor_key] = vendor_map
    settings.inspection_excludes = new_map
    save_settings(settings)


def _set_excludes_vendor(settings: AppSettings, vendor: str, exclude_all: bool) -> None:
    vendor_key = vendor.lower()
    if not exclude_all:
        if vendor_key in settings.inspection_excludes:
            settings.inspection_excludes.pop(vendor_key, None)
            save_settings(settings)
        return

    vendor_map: dict[str, list[str]] = {}
    for os_name in INSPECTION_COMMANDS.get(vendor, {}).keys():
        os_key = os_name.lower()
        items = _build_exclude_list_for_os(vendor, os_name)
        if items:
            vendor_map[os_key] = items
    if vendor_map:
        settings.inspection_excludes[vendor_key] = vendor_map
    else:
        settings.inspection_excludes.pop(vendor_key, None)
    save_settings(settings)


def _set_excludes_os(settings: AppSettings, vendor: str, os_name: str, exclude_all: bool) -> None:
    vendor_key = vendor.lower()
    os_key = os_name.lower()
    vendor_map = settings.inspection_excludes.setdefault(vendor_key, {})
    if exclude_all:
        items = _build_exclude_list_for_os(vendor, os_name)
        if items:
            vendor_map[os_key] = items
            settings.inspection_excludes[vendor_key] = vendor_map
        else:
            vendor_map.pop(os_key, None)
            if not vendor_map:
                settings.inspection_excludes.pop(vendor_key, None)
        save_settings(settings)
        return

    if os_key in vendor_map:
        vendor_map.pop(os_key, None)
        if vendor_map:
            settings.inspection_excludes[vendor_key] = vendor_map
        else:
            settings.inspection_excludes.pop(vendor_key, None)
        save_settings(settings)


def _collect_parsing_items(vendor: str, os_name: str) -> list[dict]:
    vendor_key = vendor.lower()
    os_key = os_name.lower()
    rules_by_command = PARSING_RULES.get(vendor_key, {}).get(os_key, {})
    items: list[dict] = []
    seen: set[str] = set()

    for command, rules in rules_by_command.items():
        if not isinstance(rules, dict):
            continue

        def add_item(column: str) -> None:
            if not column:
                return
            parse_id = f"{command}::{column}"
            if parse_id in seen:
                return
            seen.add(parse_id)
            items.append({
                "command": command,
                "column": column,
                "id": parse_id,
                "label": f"{column}  ({command})"
            })

        if "custom_parser" in rules:
            column = rules.get("output_column", "").strip()
            if column:
                add_item(column)
        elif "pattern" in rules:
            column = rules.get("output_column", "").strip()
            if column:
                add_item(column)
            process = rules.get("process", {})
            if isinstance(process, dict):
                process_column = str(process.get("output_column", "")).strip()
                if process_column:
                    add_item(process_column)
        elif "patterns" in rules:
            for pattern_rule in rules.get("patterns", []):
                if not isinstance(pattern_rule, dict):
                    continue
                if "custom_parser" in pattern_rule:
                    column = str(pattern_rule.get("output_column", "")).strip()
                    if column:
                        add_item(column)
                output_columns = pattern_rule.get("output_columns", [])
                if isinstance(output_columns, list):
                    for col in output_columns:
                        if isinstance(col, str) and col.strip():
                            add_item(col.strip())
                column = str(pattern_rule.get("output_column", "")).strip()
                if column:
                    add_item(column)
                process = pattern_rule.get("process", {})
                if isinstance(process, dict):
                    process_column = str(process.get("output_column", "")).strip()
                    if process_column:
                        add_item(process_column)
        else:
            column = rules.get("output_column", "").strip()
            if column:
                add_item(column)

    return items


def show_inspection_exclude_menu(settings: AppSettings) -> None:
    while True:
        _clear()
        vendors = sorted(INSPECTION_COMMANDS.keys())
        console.print(Panel(
            "점검 제외 설정\n벤더를 선택하세요.",
            title="[bold cyan]점검 제외 - 벤더 선택[/bold cyan]",
            border_style="cyan",
            expand=False,
        ))
        console.print()

        choices: list = [{"name": v, "value": v} for v in vendors]
        choices.append(Separator())
        choices.append({"name": "점검 항목 모두 포함", "value": "__include_all"})
        choices.append({"name": "점검 항목 모두 제외", "value": "__exclude_all"})
        choices.append(Separator())
        choices.append({"name": "뒤로가기", "value": None})

        choice = inquirer.select(
            message="벤더:",
            choices=choices,
            pointer="▶",
        ).execute()

        if choice is None:
            return
        if choice == "__include_all":
            if ask_yes_no("모든 점검 항목을 포함으로 변경할까요?"):
                _set_excludes_all(settings, False)
                console.print("[green]모든 점검 항목이 포함으로 변경되었습니다.[/green]")
            continue
        if choice == "__exclude_all":
            if ask_yes_no("모든 점검 항목을 제외로 변경할까요?"):
                _set_excludes_all(settings, True)
                console.print("[yellow]모든 점검 항목이 제외로 변경되었습니다.[/yellow]")
            continue
        _show_inspection_exclude_os_menu(settings, choice)


def _show_inspection_exclude_os_menu(settings: AppSettings, vendor: str) -> None:
    while True:
        _clear()
        os_list = sorted(INSPECTION_COMMANDS.get(vendor, {}).keys())
        console.print(Panel(
            f"벤더: [bold]{vendor}[/bold]\nOS를 선택하세요.",
            title="[bold cyan]점검 제외 - OS 선택[/bold cyan]",
            border_style="cyan",
            expand=False,
        ))
        console.print()

        choices: list = [{"name": os_name, "value": os_name} for os_name in os_list]
        choices.append(Separator())
        choices.append({"name": "해당 벤더 항목 모두 포함", "value": "__include_all"})
        choices.append({"name": "해당 벤더 항목 모두 제외", "value": "__exclude_all"})
        choices.append(Separator())
        choices.append({"name": "뒤로가기", "value": None})

        choice = inquirer.select(
            message="OS:",
            choices=choices,
            pointer="▶",
        ).execute()

        if choice is None:
            return
        if choice == "__include_all":
            if ask_yes_no(f"벤더 '{vendor}'의 점검 항목을 모두 포함으로 변경할까요?"):
                _set_excludes_vendor(settings, vendor, False)
                console.print(f"[green]벤더 '{vendor}'의 점검 항목이 모두 포함으로 변경되었습니다.[/green]")
            continue
        if choice == "__exclude_all":
            if ask_yes_no(f"벤더 '{vendor}'의 점검 항목을 모두 제외로 변경할까요?"):
                _set_excludes_vendor(settings, vendor, True)
                console.print(f"[yellow]벤더 '{vendor}'의 점검 항목이 모두 제외로 변경되었습니다.[/yellow]")
            continue
        _show_inspection_exclude_commands_menu(settings, vendor, choice)


def _show_inspection_exclude_commands_menu(settings: AppSettings, vendor: str, os_name: str) -> None:
    while True:
        _clear()
        excluded = _get_excluded_set(settings, vendor, os_name)
        items = _collect_parsing_items(vendor, os_name)

        console.print(Panel(
            f"벤더: [bold]{vendor}[/bold] / OS: [bold]{os_name}[/bold]\n"
            "Enter로 포함/제외를 전환합니다.",
            title="[bold cyan]점검 제외 - 항목 선택[/bold cyan]",
            border_style="cyan",
            expand=False,
        ))
        console.print()

        if not items:
            console.print("[dim]파싱 항목이 없습니다.[/dim]")
            input("Enter를 누르면 돌아갑니다.")
            return

        choices: list = []
        for item in items:
            parse_id = item["id"]
            command = item["command"]
            is_excluded = _is_parse_excluded(excluded, command, parse_id)
            status = "제외" if is_excluded else "포함"
            marker = "✗" if is_excluded else "✓"
            choices.append({
                "name": f"[{marker} {status}] {item['label']}",
                "value": parse_id,
            })
        choices.append(Separator())
        choices.append({"name": "해당 OS 항목 모두 포함", "value": "__include_all"})
        choices.append({"name": "해당 OS 항목 모두 제외", "value": "__exclude_all"})
        choices.append(Separator())
        choices.append({"name": "뒤로가기", "value": None})

        choice = inquirer.select(
            message="항목:",
            choices=choices,
            pointer="▶",
        ).execute()

        if choice is None:
            return
        if choice == "__include_all":
            if ask_yes_no(f"OS '{os_name}'의 점검 항목을 모두 포함으로 변경할까요?"):
                _set_excludes_os(settings, vendor, os_name, False)
                console.print(f"[green]OS '{os_name}'의 점검 항목이 모두 포함으로 변경되었습니다.[/green]")
            continue
        if choice == "__exclude_all":
            if ask_yes_no(f"OS '{os_name}'의 점검 항목을 모두 제외로 변경할까요?"):
                _set_excludes_os(settings, vendor, os_name, True)
                console.print(f"[yellow]OS '{os_name}'의 점검 항목이 모두 제외로 변경되었습니다.[/yellow]")
            continue
        _toggle_exclude(settings, vendor, os_name, choice)
