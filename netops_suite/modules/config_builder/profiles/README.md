# Cisco 템플릿 예시

기본 템플릿은 Cisco 장비 기준으로 작성되어 있습니다.

포함된 예시:

- `CISCO_IOS_L2_ACCESS_BASE`
- `CISCO_IOSXE_L3_DISTRIBUTION_BASE`
- `CISCO_IOSXE_EDGE_PORT_BASE`
- `SAMPLE_COMPREHENSIVE_REFERENCE`

이 예시들은 아래 내용을 참고할 수 있도록 구성되어 있습니다.

- 필수값
- 선택값
- 기본값
- 블록 선택 패널로 제어되는 블록
- 정수 형식 검증
- IPv4 형식 검증
- `profile_id` 불일치 동작
- 한국어 설명 필드 작성 예시

새 템플릿을 만들 때는 이 파일들을 기준 예시로 사용하면 됩니다.

특히 `sample_comprehensive_reference.yaml`은 아래를 한 파일에서 함께 볼 수 있도록 만든 종합 참고용 샘플입니다.

- 필수값 / 선택값
- 기본값 적용
- `string / ipv4 / bool / int` 타입
- 블록 선택 패널
- 시크릿 성격 변수(`*_secret`, `*password*`)
- 장비 설정 정보 파일 컬럼명과 템플릿 변수명의 연결 방식
