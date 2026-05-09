# NetOps Suite

NetOps Suite는 Windows 현장 네트워크 작업을 한 앱에서 처리하기 위한 PySide6 기반 GUI 도구입니다.
기존 `netops-toolkit`, `netops-inspector`, `switch-config-builder` 기능을 통합해 인터페이스 설정, 진단, 파일 전송, 성능 테스트, 장비 점검, 설정 생성까지 한 화면에서 사용할 수 있게 정리했습니다.

## 주요 기능

### 인터페이스

- Windows 네트워크 어댑터 조회
- DHCP / 고정 IP 전환
- 게이트웨이 / DNS 적용
- IP 프로파일 저장, 수정, 삭제, 재적용

### 진단

- 서브넷 계산
- ARP 스캔
- MAC / BSSID 기반 OUI 벤더 조회
- Ping / TCPing / nslookup / tracert / pathping
- DNS 캐시 비우기
- Ping / TCP 결과 CSV export와 개별 로그 저장

### 무선

- 현재 Wi-Fi 상태 조회
- 주변 AP 스캔
- SSID, BSSID, 채널, 밴드, 보안, 신호, 속도 표시
- 검색, 정렬, 자동 새로고침
- OUI 기반 AP 벤더 표시

### 전송 / 성능

- FTP / FTPS / SFTP / SCP / TFTP 클라이언트
- FTP / SCP / TFTP 임시 서버
- 전송 프로파일 관리
- iperf3 클라이언트 / 서버 모드
- winget 기반 iperf3 설치 / 업데이트 보조
- 공개 iperf3 서버 목록 캐시

### 장비 점검

- Excel 인벤토리 불러오기와 스키마 검증
- SSH / Telnet 기반 점검, 백업, 점검+백업
- TXT / Excel 사용자 명령 일괄 실행
- 장비별 진행률과 세션 로그
- 결과 Excel, 설정 백업 TXT, raw command output 저장
- 벤더 / 모델 / OS별 점검 템플릿 작성
- 줄 번호 / 값 번호 기반 쉬운 파싱과 정규식 / Python 고급 추출 지원

### 설정 생성

- YAML 프로파일 기반 Jinja2 설정 렌더링
- CSV / XLSX 장비값 편집
- 변수 검증, 블록 선택, CLI 미리보기
- 선택 장비 CLI 복사, 복사+다음, 적용 완료 상태
- 전체 / 선택 / 장비별 TXT 저장

### 산출물

- 최근 점검 결과, 백업, 세션 로그, raw output, 설정 생성 파일, 앱 로그 확인
- 파일 열기, 폴더 열기, 경로 복사
- 운영 민감 데이터 저장 위치 표시

## 참고용 예시 템플릿

이 저장소의 예시 템플릿은 운영용 정답이 아니라 사용자가 구조를 보고 자기 환경에 맞게 복사/수정하기 위한 참고 자료입니다.
실제 장비 모델, OS, OS 버전에 따라 명령어와 출력 형식이 다르므로 그대로 운영 반영하지 마세요.

- 장비 점검 참고 예시: `netops_suite/modules/inspector/vendor_templates/reference__inspection_examples.yaml`
- Python 추출 함수 참고 예시: `netops_suite/modules/inspector_runtime/custom_parsers.example.py`
- 설정 생성 참고 프로파일: `netops_suite/modules/config_builder/profiles/sample_*.yaml`
- 설정 생성 장비값 참고 CSV: `netops_suite/modules/config_builder/device_values/sample_*.csv`

## 실행 방법

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

개발 편의 스크립트:

```powershell
.\scripts\run_dev.ps1
```

## 관리자 권한

다음 작업은 Windows 관리자 권한이 필요할 수 있습니다.

- DHCP 적용
- 고정 IP 적용
- 게이트웨이 / DNS 변경
- 일부 어댑터 고급 설정 변경
- 일부 DNS 캐시 작업

조회, Ping, TCPing, nslookup, tracert, pathping, Wi-Fi 스캔, 설정 생성, 장비 점검 기능은 일반 권한에서도 대부분 사용할 수 있습니다.

## 데이터 저장 위치

소스 폴더에서 실행하면 기본적으로 프로젝트 폴더 아래의 `config/`, `logs/`, `inspector/`, `config_builder/` 등을 사용합니다.
설치본이 `C:\Program Files` 같은 보호된 경로에서 실행되면 런타임 데이터는 `%LOCALAPPDATA%\NetOps Suite` 아래로 저장됩니다.

운영 민감 데이터가 포함될 수 있는 항목:

- 장비 점검 결과 Excel
- 설정 백업 TXT
- 장비별 세션 로그
- 사용자 명령 raw output
- 생성된 CLI 설정 파일

## 업데이트

- 기본 GitHub 저장소: `nowthatscomedy/netops-suite`
- 설치 파일 패턴: `NetOpsSuite-setup.*\.exe$`
- GitHub Releases의 최신 버전을 확인합니다.
- 설치 파일은 SHA-256 검증 후 실행합니다.
- prerelease 포함 여부는 설정에서 선택할 수 있습니다.

## 테스트

```powershell
python -m pytest
python -m compileall main.py app netops_suite tests
```

## 빌드

```powershell
pip install -r requirements.txt
pip install pyinstaller
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -Version 1.0.3 -Clean
```

Windows 설치 파일 빌드에는 Inno Setup 6가 필요합니다.

GitHub Actions 릴리즈 빌드는 `workflow_dispatch`로 수동 실행하며, 저장소 공개 전환이나 `main` push와
릴리즈 게시를 분리합니다.
공개 설치 파일에는 `LICENSE`와 `THIRD_PARTY_NOTICES.md`를 포함해야 합니다.
Windows 설치 파일 코드 서명은 선택 사항입니다. 서명이 필요할 때만 로컬에서 인증서 경로와 비밀번호를
지정하고 `-RequireCodeSigning`을 함께 사용합니다.

로컬에서 서명 빌드를 확인할 때는 다음처럼 실행합니다.

```powershell
$env:WINDOWS_CODESIGN_CERT_PATH = "C:\path\codesign.pfx"
$env:WINDOWS_CODESIGN_CERT_PASSWORD = "<pfx password>"
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -Version 1.0.3 -Clean -RequireCodeSigning
```

## 프로젝트 구조

```text
netops-suite/
  main.py
  requirements.txt
  app/
    main_window.py
    services/
    ui/
  netops_suite/
    core/
    modules/
      config_builder/
      inspector/
      inspector_runtime/
    ui/
  config/
  logs/
  assets/
  installer/
  scripts/
  tests/
```
