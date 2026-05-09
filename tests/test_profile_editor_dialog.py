from app.models.profile_models import IPProfile
from app.ui.dialogs.profile_editor_dialog import ProfileEditorDialog


def test_profile_editor_preserves_hidden_metadata(qapp):
    profile = IPProfile(
        name="Printer",
        mode="static",
        interface_name="Ethernet",
        local_ip="192.168.0.10",
        prefix=24,
        gateway="192.168.0.1",
        dns=["8.8.8.8"],
        target_vendor="HP",
        target_ip="192.168.0.50",
        notes="Keep metadata",
    )

    dialog = ProfileEditorDialog(profile=profile)
    dialog.name_edit.setText("Printer-Edited")

    edited = dialog.profile_data()

    assert edited.name == "Printer-Edited"
    assert edited.target_vendor == "HP"
    assert edited.target_ip == "192.168.0.50"
    assert edited.notes == "Keep metadata"
