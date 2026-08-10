# NetOps Suite 기여 가이드

## 개발 완료 기준

기능 변경은 코드, 테스트, 사용자 가이드, 생성된 오프라인 번들이 함께 검토 가능한 상태여야 완료됩니다. 특히 버튼 이름, 기본값, 권한, 저장 위치, 오류 메시지, 작업 결과가 달라지면 사용자 영향 변경입니다.

가이드의 단일 원본은 `docs/user/<locale>/`이고 등록 정보는 `docs/guide_manifest.json`입니다. `app/resources/guides/`는 앱에 포함되는 결정적 생성물이므로 직접 고치지 마세요.

## 새 기능 또는 변경 기능 문서화

사용자에게 보이는 새 기능은 먼저 `app/assistant/capabilities.py`의 공개 기능 계약에 등록합니다. 공개 화면명, 가이드 ID·경로, 검색 의도, 지원 수준, 입력·제약·위험도와 연결된 내부 도구를 작성한 뒤 다음 명령을 실행합니다.

```powershell
python scripts/generate_guides.py sync-capabilities
```

이 명령은 기존 수동 가이드 본문을 덮어쓰지 않고 manifest의 공개 메타데이터를 동기화합니다. 새로운 `guide_id`이면 표준 구역과 `TODO`가 있는 비파괴 초안을 자동 생성합니다. 생성된 모든 `TODO`는 실제 절차로 교체해야 빌드할 수 있습니다.

기능 계약을 사용하지 않는 참고 문서는 기존 안전한 scaffold 명령으로 시작할 수 있습니다.

```powershell
python scripts/generate_guides.py scaffold diagnostics.example `
  --title "예제 진단" `
  --parent-id diagnostics `
  --route diagnostics:example `
  --source-path app/ui/tabs/diagnostics/example.py `
  --source-path app/services/example_service.py `
  --keyword "예제 진단" `
  --risk low
```

명령은 기존 ID나 문서를 덮어쓰지 않습니다. 생성된 문서의 모든 필수 구역과 `TODO`를 실제 사용자 관점의 내용으로 교체하세요. 기존 기능은 매니페스트의 `source_paths`로 연결된 문서를 직접 갱신합니다.

매니페스트의 각 `guides` 항목은 다음 계약을 사용합니다.

- `id`: 고유한 소문자 점 표기 기능 ID
- `parent_id`: 상위 가이드 ID 또는 `null`
- `title`, `locale`, `path`: 표시 제목, 언어, 원본 Markdown 경로
- `anchor`: 문서 내 안정적인 소문자 ASCII 앵커 또는 빈 문자열
- `route`: 앱 도움말 문맥 경로(예: `diagnostics:ping`)
- `source_paths`: 기능을 구현하는 저장소 상대 경로 또는 glob
- `keywords`, `risk`: 검색어와 `low`/`medium`/`high` 위험도
- `qa_capture_ids`: 오프스크린 QA 시나리오 ID 목록
- `capability_ids`: 해당 가이드와 연결된 공개 기능 계약 ID 목록(동기화 명령이 관리)

가이드에는 매니페스트의 `required_sections`가 모두 있어야 하며, 로컬 링크·이미지는 저장소 안의 실제 파일만 가리켜야 합니다. 외부 연결 없이 읽을 수 있도록 locale 아래의 에셋도 번들에 복사됩니다.

## 로컬 검증

```powershell
python scripts/validate_guides.py
python scripts/generate_guides.py sync-capabilities --check
python scripts/generate_guides.py build
python scripts/generate_guides.py build --check
test.bat
```

`build --check`는 임시 위치에 다시 생성한 결과를 커밋된 번들과 바이트 단위로 비교하며 누락, 오래된 파일, 불필요하게 남은 파일을 모두 실패 처리합니다.

PR에서는 변경된 기능 소스가 `source_paths`로 어떤 가이드 ID에 연결되는지 추가 검사합니다. 연결된 원본 가이드가 함께 바뀌지 않았거나 사용자-facing Python 파일이 어느 가이드에도 매핑되지 않으면 검사가 실패합니다.

## `guide-not-required` 예외

사용자 행동, 출력, 문구, 권한, 저장 형식에 영향이 없는 내부 변경만 예외 대상입니다. 기여자는 PR 본문의 `Guide exception reason:` 줄에 구체적인 이유를 적고 유지관리자에게 검토를 요청합니다. 유지관리자가 `guide-not-required` 라벨을 적용해야 영향도 검사만 예외 처리됩니다. 매니페스트 오류, 깨진 링크, 미완성 문구, 오래된 생성물 등 구조 검사는 라벨로 우회할 수 없습니다.
