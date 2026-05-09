from app import __version__ as app_version
from app.version import __version__ as source_version


def test_version_contract():
    assert app_version == source_version


def test_release_workflow_defaults_to_current_version():
    workflow = open(".github/workflows/release.yml", encoding="utf-8").read()
    expected_tag = f"v{source_version}"

    assert f'default: "{expected_tag}"' in workflow
    assert f"example: {expected_tag}" in workflow
