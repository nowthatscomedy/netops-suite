# 설정 생성 참고용 프로파일

이 폴더의 `sample_*.yaml` 파일은 운영 표준 설정이 아니라 프로파일 작성법을 보여주는 참고 예시입니다.
실제 장비에 그대로 반영하지 말고, 현장 표준과 장비 OS 버전에 맞게 복사해서 수정하세요.

포함 예시:

- `CISCO_IOS_L2_ACCESS_BASE`: L2 Access 스위치 기본 구조
- `CISCO_IOSXE_L3_DISTRIBUTION_BASE`: L3 Distribution 스위치 기본 구조
- `CISCO_IOSXE_EDGE_PORT_BASE`: Edge access port 블록 구조
- `SAMPLE_COMPREHENSIVE_REFERENCE`: 변수 타입, 기본값, 필수값, block skip, 시크릿 변수, 검증 오류를 한 번에 보기 위한 종합 참고 예시

참고 포인트:

- 변수 타입: `string`, `ipv4`, `bool`, `int`
- 필수값: `required: true`
- 기본값: `default`
- 자동 증가: `auto_increment`
- 블록 선택: UI에서 블록별 출력 여부를 제어
- 시크릿 성격 변수: `secret`, `password`, `key`, `community`가 들어간 컬럼은 UI에서 마스킹될 수 있음

