from __future__ import annotations

from InquirerPy import inquirer

from core.i18n import t


def get_password_from_cli() -> str | None:
    result = inquirer.secret(
        message=t("ui.encrypted_excel_password"),
        mandatory=False,
    ).execute()
    if not result:
        return None
    return result
