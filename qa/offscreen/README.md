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
- Wi-Fi 스캔과 필터
- 프로그램 및 저장 위치 설정
- AI 채팅의 사용자→시스템→도구 결과 순서와 이미지 붙여넣기
- 장비 점검 프로파일의 점검/백업 명령 분리와 YAML 미리보기
- 1024×680, 1280×800, 1600×900 레이아웃 기본 조건

각 단계의 PNG, `report.json`, `report.md`가 출력 폴더에 저장됩니다. 실패해도 가능한
나머지 시나리오는 계속 실행되어 한 번에 전체 결함 목록을 확인할 수 있습니다.
