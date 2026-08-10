# NetOps Suite 작업 지침

이 지침은 저장소 전체에 적용됩니다.

## 사용자 영향 변경의 완료 조건

사용자가 보거나 조작하는 기능을 추가·변경·삭제할 때는 한 변경에서 다음을 함께 완료합니다.

1. 기능 코드와 자동화 테스트를 수정합니다.
2. `docs/guide_manifest.json`에서 가장 가까운 가이드의 `source_paths` 매핑을 확인하거나 새 매핑을 추가합니다.
3. 연결된 `docs/user/<locale>/` 원본 가이드를 실제 UI와 동일하게 수정합니다.
4. 화면 증거가 필요한 변경이면 `qa/offscreen/scenarios.json`의 캡처와 `qa_capture_ids`를 갱신합니다.
5. `python scripts/generate_guides.py build`로 오프라인 번들을 재생성합니다.
6. 아래 검증을 통과시킵니다.

```powershell
python scripts/validate_guides.py
python scripts/generate_guides.py build --check
test.bat
```

가이드 원본은 `docs/user/`이며 `app/resources/guides/`는 생성물입니다. 생성물을 직접 편집하지 않습니다. 새 기능은 다음 명령으로 안전한 초안을 만들 수 있습니다.

```powershell
python scripts/generate_guides.py scaffold <feature.id> --title "표시 제목" --parent-id <parent.id> --source-path app/path/to/feature.py
```

초안의 모든 `TODO`를 실제 절차·성공 조건·오류 해결 내용으로 교체한 뒤 번들을 빌드합니다.

## 예외 정책

내부 리팩터링처럼 사용자 행동이나 결과가 전혀 바뀌지 않을 때만 저장소 유지관리자가 PR에 `guide-not-required` 라벨을 적용할 수 있습니다. PR 본문의 `Guide exception reason:` 줄에 구체적인 근거가 있어야 합니다. 에이전트와 기여자는 이 라벨을 스스로 적용하거나 가이드 구조·생성 검증을 우회해서는 안 됩니다.

