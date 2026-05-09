from app.models.scp_models import ScpProfile
from app.ui.dialogs.scp_profile_dialog import ScpProfileDialog


def test_scp_profile_dialog_round_trip(qapp):
    profile = ScpProfile(
        name="Field SCP",
        host="10.0.0.15",
        port=22,
        username="engineer",
        remote_path="/drop",
        timeout_seconds=30,
    )

    dialog = ScpProfileDialog(profile=profile)
    dialog.name_edit.setText("Field SCP Backup")
    dialog.remote_path_edit.setText("logs")
    dialog.timeout_edit.setText("45")

    edited = dialog.profile_data()

    assert edited.name == "Field SCP Backup"
    assert edited.host == "10.0.0.15"
    assert edited.port == 22
    assert edited.username == "engineer"
    assert edited.remote_path == "logs"
    assert edited.timeout_seconds == 45
