from __future__ import annotations

from pathlib import Path

import pytest
from jinja2.sandbox import SandboxedEnvironment
from PySide6.QtWidgets import QMessageBox

from netops_suite.modules.config_builder.switch_configurator.authoring import (
    ProfileBuilderValidation,
    ProfileSaveConflictError,
    inspect_profile_save_target,
    save_profile_yaml_to_directory,
    validate_profile_builder_state,
)
from netops_suite.modules.config_builder.switch_configurator.engine import ConfigEngine
from netops_suite.modules.config_builder.switch_configurator.io_utils import (
    load_profiles_from_directory,
    parse_profile_yaml,
)
from netops_suite.modules.config_builder.switch_configurator.profile_builder_dialog import (
    ProfileBuilderDialog,
)


def _state(*, line: str = "hostname {{ hostname }}", profile_id: str = "TEST_PROFILE"):
    return {
        "id": profile_id,
        "vendor": "Cisco",
        "model": "C9300",
        "firmware": "IOS-XE 17.x",
        "description": "test",
        "variables": [
            {
                "name": "hostname",
                "required": True,
                "type": "string",
                "default_input": "",
                "description": "",
                "auto_increment": "none",
            }
        ],
        "blocks": [{"name": "base", "lines_text": line}],
    }


def _valid_yaml(*, profile_id: str = "TEST_PROFILE", model: str = "C9300") -> str:
    validation = validate_profile_builder_state(
        {**_state(profile_id=profile_id), "model": model}
    )
    assert validation.is_valid, validation.issues
    return validation.yaml_text


def test_config_engine_uses_strict_sandbox_and_save_validation_renders_sample():
    validation = validate_profile_builder_state(_state())

    assert validation.is_valid
    assert validation.rendered_preview == "hostname sample-value"
    assert isinstance(ConfigEngine({}).environment, SandboxedEnvironment)
    assert ConfigEngine({}).environment.undefined.__name__ == "StrictUndefined"


def test_profile_validation_blocks_literal_secrets_and_separates_risky_warnings():
    literal_secret = validate_profile_builder_state(
        _state(line="enable secret hardcoded-value")
    )
    risky_command = validate_profile_builder_state(_state(line="reload"))

    assert not literal_secret.is_valid
    assert any("비밀값" in issue for issue in literal_secret.issues)
    assert risky_command.is_valid
    assert any("장비 상태" in warning for warning in risky_command.warnings)
    for line in (
        "write memory",
        "copy running-config startup-config",
        "no line vty 0 4",
    ):
        validation = validate_profile_builder_state(_state(line=line))
        assert validation.is_valid
        assert validation.warnings


@pytest.mark.parametrize(
    ("literal_line", "jinja_line", "variable_name"),
    [
        (
            "radius-server key hardcoded-radius",
            "radius-server key {{ radius_key }}",
            "radius_key",
        ),
        (
            "tacacs-server key 7 hardcoded-tacacs",
            "tacacs-server key 7 {{ tacacs_key }}",
            "tacacs_key",
        ),
        (
            "key-string cipher hardcoded-key",
            "key-string cipher {{ private_key }}",
            "private_key",
        ),
        (
            "pre-shared-key ascii hardcoded-psk",
            "pre-shared-key ascii {{ wpa_psk }}",
            "wpa_psk",
        ),
        (
            "wpa-psk pass-phrase hardcoded-wpa",
            "wpa-psk pass-phrase {{ wpa_psk }}",
            "wpa_psk",
        ),
    ],
)
def test_profile_validation_blocks_network_secrets_but_allows_variable_value(
    literal_line: str,
    jinja_line: str,
    variable_name: str,
):
    literal = validate_profile_builder_state(_state(line=literal_line))
    assert not literal.is_valid
    assert any("비밀값" in issue for issue in literal.issues)

    state = _state(line=jinja_line)
    state["variables"][0]["name"] = variable_name
    jinja_result = validate_profile_builder_state(state)
    assert jinja_result.is_valid, jinja_result.issues


@pytest.mark.parametrize(
    "variable_name",
    [
        "radius_key",
        "tacacs_key",
        "wpa_psk",
        "private_key",
        "admin_pwd",
        "wifi_passphrase",
        "clientSecret",
        "accessToken",
        "authPassword",
    ],
)
def test_profile_validation_blocks_defaults_for_network_secret_variables(
    variable_name: str,
):
    state = _state(line=f"key-string {{{{ {variable_name} }}}}")
    state["variables"][0].update(
        {"name": variable_name, "default_input": "hardcoded-value"}
    )

    validation = validate_profile_builder_state(state)

    assert not validation.is_valid
    assert any(variable_name in issue and "기본값" in issue for issue in validation.issues)


def test_profile_validation_allows_default_for_public_key_variable():
    state = _state(line="description {{ public_key }}")
    state["variables"][0].update(
        {"name": "public_key", "default_input": "ssh-rsa-public-material"}
    )

    validation = validate_profile_builder_state(state)

    assert validation.is_valid, validation.issues


def test_profile_validation_rejects_literal_jinja_secret_hybrid():
    state = _state(line="radius-server key literal-{{ radius_key }}")
    state["variables"][0]["name"] = "radius_key"

    validation = validate_profile_builder_state(state)

    assert not validation.is_valid
    assert any("비밀값" in issue for issue in validation.issues)


@pytest.mark.parametrize(
    "line",
    [
        "enable secret literal-{{ admin_password }}",
        "enable secret {{ admin_password }}-literal",
    ],
)
def test_profile_validation_rejects_prefixed_and_suffixed_secret_jinja_hybrids(
    line: str,
):
    state = _state(line=line)
    state["variables"][0]["name"] = "admin_password"

    validation = validate_profile_builder_state(state)

    assert not validation.is_valid
    assert any("비밀값" in issue for issue in validation.issues)


def test_profile_validation_blocks_snmpv3_secrets_but_allows_jinja_variables():
    literal = validate_profile_builder_state(
        _state(
            line="snmp-server user monitor ops v3 auth sha AuthPass "
            "priv aes 128 PrivPass"
        )
    )
    assert not literal.is_valid
    assert any("비밀값" in issue for issue in literal.issues)

    state = _state(
        line="snmp-server user monitor ops v3 auth sha {{ snmp_auth_password }} "
        "priv aes 128 {{ snmp_priv_password }}"
    )
    state["variables"] = [
        {
            "name": name,
            "required": True,
            "type": "string",
            "default_input": "",
            "description": "",
            "auto_increment": "none",
        }
        for name in ("snmp_auth_password", "snmp_priv_password")
    ]
    jinja_result = validate_profile_builder_state(state)
    assert jinja_result.is_valid, jinja_result.issues


def test_profile_validation_allows_jinja_username_password_with_type_prefix():
    state = _state(line="username admin password 0 {{ admin_password }}")
    state["variables"][0]["name"] = "admin_password"

    validation = validate_profile_builder_state(state)

    assert validation.is_valid, validation.issues


def test_profile_save_preserves_original_filename_and_creates_backup(tmp_path: Path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    original_path = profiles_dir / "hand-written-name.yml"
    original_yaml = _valid_yaml(model="C9300")
    original_path.write_text(original_yaml, encoding="utf-8")
    replacement_yaml = _valid_yaml(model="C9400")

    target = inspect_profile_save_target(
        "TEST_PROFILE", profiles_dir, source_path=original_path
    )
    saved_path, existed = save_profile_yaml_to_directory(
        "TEST_PROFILE",
        replacement_yaml,
        profiles_dir,
        source_path=original_path,
        allow_overwrite=True,
    )

    assert target.path == original_path
    assert target.exists
    assert existed
    assert saved_path == original_path
    assert "C9400" in original_path.read_text(encoding="utf-8")
    assert original_path.with_suffix(".yml.bak").read_text(encoding="utf-8") == original_yaml


def test_profile_save_requires_explicit_overwrite_confirmation(tmp_path: Path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    existing = profiles_dir / "TEST_PROFILE.yaml"
    existing.write_text(_valid_yaml(), encoding="utf-8")

    with pytest.raises(FileExistsError, match="확인이 필요"):
        save_profile_yaml_to_directory("TEST_PROFILE", _valid_yaml(), profiles_dir)


def test_profile_save_rejects_case_insensitive_id_collision(tmp_path: Path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "unrelated-name.yaml").write_text(
        _valid_yaml(profile_id="test_profile"), encoding="utf-8"
    )

    with pytest.raises(ProfileSaveConflictError, match="프로파일 ID"):
        inspect_profile_save_target("TEST_PROFILE", profiles_dir)


def test_profile_loader_reports_case_insensitive_id_and_filename_collisions(tmp_path: Path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "alpha.yaml").write_text(
        _valid_yaml(profile_id="FIRST"), encoding="utf-8"
    )
    (profiles_dir / "bravo.yaml").write_text(
        _valid_yaml(profile_id="first"), encoding="utf-8"
    )
    (profiles_dir / "copy.yaml").write_text(
        _valid_yaml(profile_id="THIRD"), encoding="utf-8"
    )
    (profiles_dir / "COPY.yml").write_text(
        _valid_yaml(profile_id="FOURTH"), encoding="utf-8"
    )

    profiles, issues = load_profiles_from_directory(profiles_dir)

    assert "FIRST" in profiles
    assert len(profiles) == 2
    assert any("중복된 profile id" in issue.message for issue in issues)
    assert any("중복된 프로파일 파일명" in issue.message for issue in issues)


def test_profile_save_restores_original_when_reload_verification_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from netops_suite.modules.config_builder.switch_configurator import authoring

    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    target_path = profiles_dir / "TEST_PROFILE.yaml"
    original_yaml = _valid_yaml(model="C9300")
    replacement_yaml = _valid_yaml(model="C9400")
    target_path.write_text(original_yaml, encoding="utf-8")
    real_validate = authoring.validate_profile_yaml_for_save
    call_count = 0

    def fail_reload_validation(yaml_text: str, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            return ProfileBuilderValidation(
                yaml_text=yaml_text,
                profile=None,
                issues=("forced reload failure",),
            )
        return real_validate(yaml_text, **kwargs)

    monkeypatch.setattr(authoring, "validate_profile_yaml_for_save", fail_reload_validation)

    with pytest.raises(OSError, match="forced reload failure"):
        save_profile_yaml_to_directory(
            "TEST_PROFILE", replacement_yaml, profiles_dir, allow_overwrite=True
        )

    assert target_path.read_text(encoding="utf-8") == original_yaml


def test_profile_builder_does_not_overwrite_source_without_confirmation(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    source_path = profiles_dir / "original-file-name.yml"
    original_yaml = _valid_yaml(model="C9300")
    source_path.write_text(original_yaml, encoding="utf-8")
    profile = parse_profile_yaml(original_yaml, str(source_path))
    dialog = ProfileBuilderDialog(profiles_dir, profile)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.No)
    try:
        dialog.model_edit.setText("C9400")

        dialog.save_profile()

        assert dialog.saved_path is None
        assert source_path.read_text(encoding="utf-8") == original_yaml
        assert not source_path.with_suffix(".yml.bak").exists()
    finally:
        dialog.close()


def test_profile_builder_has_no_ai_draft_controls(qapp, tmp_path: Path):
    from PySide6.QtWidgets import QPushButton

    dialog = ProfileBuilderDialog(tmp_path / "profiles")
    try:
        texts = [button.text() for button in dialog.findChildren(QPushButton)]
        assert all("AI" not in text for text in texts), texts
        assert not hasattr(dialog, "ai_draft_button")
    finally:
        dialog.close()
