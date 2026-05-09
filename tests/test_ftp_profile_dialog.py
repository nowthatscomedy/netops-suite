from app.models.ftp_models import FtpProfile
from app.ui.dialogs.ftp_profile_dialog import FtpProfileDialog


def test_ftp_profile_dialog_round_trip(qapp):
    profile = FtpProfile(
        name="Field SFTP",
        protocol="sftp",
        host="10.0.0.5",
        port=22,
        username="engineer",
        remote_path="/drop",
        passive_mode=False,
        timeout_seconds=30,
    )

    dialog = FtpProfileDialog(profile=profile)
    dialog.name_edit.setText("Field SFTP 2")
    dialog.remote_path_edit.setText("logs")

    edited = dialog.profile_data()

    assert edited.name == "Field SFTP 2"
    assert edited.protocol == "sftp"
    assert edited.remote_path == "/logs"
    assert edited.passive_mode is False
    assert edited.timeout_seconds == 30
