# Qt 오프스크린 QA

이 구성은 Windows 화면이나 마우스를 제어하지 않고 `QT_QPA_PLATFORM=offscreen`에서
NetOps Suite를 실제 Qt 위젯으로 실행합니다. 임시 데이터 루트와 결정론적 서비스
대역을 사용하므로 운영 네트워크, 설정, 로그, 장비에는 영향을 주지 않습니다.

## 실행

```powershell
python scripts/run_offscreen_qa.py
```

출력 위치를 바꾸려면:

```powershell
python scripts/run_offscreen_qa.py --output C:\Temp\netops-offscreen-qa
```

실행기는 `scenarios.json`의 화면 크기와 시나리오를 읽고 다음을 검사합니다.

- 실제 내비게이션 클릭 및 키보드 이동
- 입력, 실행, 작업 중/중지 버튼 상태, 작업 완료
- Ping/TCPing 다중 대상 결과 누락
- DNS, 명령 출력, 서브넷, OUI, 파일 전송 화면 전환
- Wi-Fi 스캔과 필터, 상세 정보·로그·필터를 세 번 접고 펼친 뒤 값 보존
- 펼친 Wi-Fi 카드의 제목·값 비중첩, 텍스트 높이, 로그 viewport 120px·AP 표 viewport 180px 이상, 마지막 행·열까지 스크롤 접근
- 프로그램 및 저장 위치 설정
- 장비 작업·장비 설정 프로파일 편집기 (AI 초안 버튼이 없는지 포함)
- 주요 화면 카드 제목이 테두리 안쪽에 표시되는지와 현재 Wi-Fi 신호의 초록·주황·빨강 상태 캡처
- 설정 명령 화면 상단 프로파일 만들기·관리 버튼 접근과 장비 작업의 보조 도구 구분
- 장비 작업 프로파일의 모델 적용 범위, 점검/백업 명령 분리와 YAML 미리보기
- 시작·파일 전송·현재 작업 도움말·펼친 Wi-Fi를 포함한 10개 가이드용 1280×800 이미지 생성
- 시작 카드 6개, 진단 도구 선택과 실행 분리, 오류·실행 중·옵션 변경 표시
- 작은 창의 비모달 도움말과 작업영역 최소 폭 확인
- 1024×680, 1280×800, 1600×900에서 8개 화면 주요 조작부의 실제 가시영역과 클릭 영역
- 같은 세 크기에서 Wi-Fi 상세·로그·필터를 모두 펼친 상태의 글자, 표, 로그와 하단 컨트롤 접근
- 같은 세 크기에서 장비 목록 작성 안내·지원 장비를 세 번 접고 펼친 뒤 카드 글자, 예시표 6행 전체 표시와 검증 버튼 접근
- 각 크기의 대표 진단 화면에서 기본·입력 오류·실행 중·완료·도움말 상태와 F1 포커스 복원

각 단계의 PNG, `report.json`, `report.md`가 출력 폴더에 저장됩니다. 실패해도 가능한
나머지 시나리오는 계속 실행되어 한 번에 전체 결함 목록을 확인할 수 있습니다.

가이드에 포함할 주요 화면 이미지를 안정된 파일명으로 함께 내보내려면 다음처럼
실행합니다.

```powershell
python scripts/run_offscreen_qa.py `
  --config qa\offscreen\guide_scenarios.json `
  --output C:\Temp\netops-offscreen-qa `
  --guide-assets-output docs\user\ko\assets\generated
```

`guide_asset`이 지정된 시나리오만 복사되며, 대상 폴더에는 이미지와 함께
`capture-manifest.json`이 생성됩니다.

캡처는 가이드 원본을 직접 읽으므로, 새 도움말을 캡처한 뒤 번들을 빌드할 수 있습니다.
화면에 보이지 않는 컨트롤은 조작하지 않으며 스크롤 영역의 대상은 먼저 화면에 표시합니다.

Qt 배율 125%·150%에서도 같은 흐름을 검사하려면 별도 출력 폴더를 사용합니다.

```powershell
$env:QT_SCALE_FACTOR = '1.25'
python scripts/run_offscreen_qa.py --output .codex_artifacts/ux-redesign/scale-125
$env:QT_SCALE_FACTOR = '1.5'
python scripts/run_offscreen_qa.py --output .codex_artifacts/ux-redesign/scale-150
Remove-Item Env:QT_SCALE_FACTOR
```

이 검사는 Qt의 배율 적용을 확인합니다. 물리 모니터 이동, 실제 장비 연결, 스크린리더 검증은 별도입니다.
