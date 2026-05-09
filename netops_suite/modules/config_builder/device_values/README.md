# 장비값 CSV 참고 예시

이 폴더의 CSV 파일은 Config Builder 프로파일 작성법을 이해하기 위한 참고용 장비값입니다.
실제 운영 장비 정보나 실제 계정/암호가 아니며, 그대로 장비에 반영하지 마세요.

포함 예시:

- `sample_cisco_ios_l2_access_base_devices.csv`
- `sample_cisco_iosxe_edge_port_base_devices.csv`
- `sample_cisco_iosxe_l3_distribution_base_devices.csv`
- `sample_comprehensive_reference_devices.csv`

작성 규칙:

- `profile_id`는 사용할 YAML 프로파일의 `id`와 같아야 합니다.
- `device_id`는 장비를 식별하기 위한 값입니다.
- 나머지 컬럼명은 YAML `variables`의 변수명과 맞춰 작성합니다.
- `sample_case`, `expected_result`, `note`처럼 프로파일에 없는 컬럼은 참고 설명용으로 둘 수 있습니다.
- 시크릿 예시는 `CHANGE_ME_*` 같은 더미값만 사용합니다.

