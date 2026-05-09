from __future__ import annotations

import ipaddress
import logging
from collections import Counter
from typing import Any

import pandas as pd

from core.custom_exceptions import ValidationError
from core.i18n import t
from core.settings import REQUIRED_INPUT_COLUMNS, canonicalize_input_column_name
from vendors import INSPECTION_COMMANDS

logger = logging.getLogger(__name__)


def _validate_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def _validate_port(port: object) -> bool:
    try:
        port_num = int(port)
        return 1 <= port_num <= 65535
    except (TypeError, ValueError):
        return False


def _validate_connection_type(connection_type: object) -> bool:
    return str(connection_type).strip().lower() in {"ssh", "telnet"}


def normalize_device_dataframe(
    df: pd.DataFrame,
    input_column_aliases: dict[str, str] | None = None,
) -> pd.DataFrame:
    normalized = df.copy()
    normalized_columns: list[str] = []
    for column in normalized.columns:
        canonical = canonicalize_input_column_name(column, input_column_aliases)
        normalized_columns.append(canonical or str(column).strip().lower())

    duplicated_columns = sorted(
        [name for name, count in Counter(normalized_columns).items() if count > 1],
    )
    if duplicated_columns:
        raise ValidationError(
            t(
                "validator.duplicate_columns",
                columns=", ".join(duplicated_columns),
            ),
        )

    normalized.columns = normalized_columns
    return normalized


def validate_device_info(device: dict[str, Any]) -> tuple[bool, str]:
    for field in REQUIRED_INPUT_COLUMNS:
        if field not in device or pd.isna(device[field]):
            message = t(
                "validator.required_field_missing",
                field=field,
                ip=device.get("ip", "N/A"),
            )
            logger.error(message)
            return False, message

    ip = str(device["ip"]).strip()
    if not _validate_ip(ip):
        return False, t("validator.invalid_ip", ip=ip)

    connection_type = str(device["connection_type"]).strip()
    if not _validate_connection_type(connection_type):
        return False, t(
            "validator.invalid_connection_type",
            connection_type=connection_type,
            ip=ip,
        )

    port = device["port"]
    if not _validate_port(port):
        return False, t("validator.invalid_port", port=port, ip=ip)

    vendor = str(device["vendor"]).strip().lower()
    os_model = str(device["os"]).strip().lower()

    if vendor not in INSPECTION_COMMANDS:
        return False, t("validator.unsupported_vendor", vendor=vendor, ip=ip)
    if os_model not in INSPECTION_COMMANDS.get(vendor, {}):
        return False, t(
            "validator.unsupported_os",
            os_model=os_model,
            vendor=vendor,
            ip=ip,
        )

    return True, ""


def validate_dataframe(
    df: pd.DataFrame,
    input_column_aliases: dict[str, str] | None = None,
) -> pd.DataFrame:
    if df.empty:
        raise ValidationError(t("validator.empty_excel"))

    normalized_df = normalize_device_dataframe(df, input_column_aliases)

    missing_columns = [col for col in REQUIRED_INPUT_COLUMNS if col not in normalized_df.columns]
    if missing_columns:
        raise ValidationError(
            t("validator.missing_columns", columns=", ".join(missing_columns)),
        )

    if normalized_df["ip"].duplicated().any():
        duplicated_ips = normalized_df[normalized_df["ip"].duplicated()]["ip"].astype(str).tolist()
        raise ValidationError(t("validator.duplicate_ips", ips=", ".join(duplicated_ips)))

    all_errors: list[str] = []
    for _, device in normalized_df.iterrows():
        is_valid, error_message = validate_device_info(device.to_dict())
        if not is_valid:
            all_errors.append(error_message)

    if all_errors:
        raise ValidationError(
            t("validator.device_info_errors", errors="\n".join(all_errors)),
        )

    return normalized_df
