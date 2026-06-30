from __future__ import annotations

from app.ui.startup_loading import StartupLoadingWindow


def test_startup_loading_window_tracks_progress(qapp):
    window = StartupLoadingWindow("1.2.3")
    try:
        window.set_step(2, "설정 파일 준비", "프로파일을 불러옵니다.", 42)

        assert window.progress_bar.value() == 42
        assert window.stage_label.text() == "설정 파일 준비"
        assert window.detail_label.text() == "프로파일을 불러옵니다."
        assert "[진행]" in window.step_list.item(2).text()

        window.complete("준비 완료")

        assert window.progress_bar.value() == 100
        assert window.stage_label.text() == "준비 완료"
        assert all("[완료]" in window.step_list.item(index).text() for index in range(window.step_list.count()))
    finally:
        window.close()
