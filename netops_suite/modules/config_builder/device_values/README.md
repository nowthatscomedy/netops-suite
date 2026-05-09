# 장비 설정 정보 안내

기본 장비 설정 정보 예시는 Cisco 기준으로 작성되어 있습니다.

## 종합 참고용 파일

- `sample_comprehensive_reference_devices.csv`

이 파일은 `profiles/sample_comprehensive_reference.yaml`과 한 세트로 보는 종합 예시입니다.

포함된 상황:

- 정상 생성
- 기본값 자동 적용
- 조건 블록 on/off
- 액세스 포트 + Voice VLAN 예시
- 필수값 누락
- 잘못된 bool / int / IPv4
- 없는 `profile_id`
- 비어 있는 `profile_id`
- `device_id` 중복
- `hostname` 중복
- 관리 IP 중복
- 게이트웨이/서브넷 대역 불일치

참고용 컬럼 설명:

- `sample_case`: 샘플 케이스 이름
- `expected_result`: 예상 결과
- `note`: 왜 이 행을 넣었는지 설명

추가 컬럼이 있어도 괜찮습니다.
선택한 템플릿에 선언되지 않은 컬럼은 렌더링 시 무시됩니다.
