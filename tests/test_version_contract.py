from app import __version__ as app_version
from app.version import __version__ as source_version


def test_version_contract():
    assert app_version == source_version
