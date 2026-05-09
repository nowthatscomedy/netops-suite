"""
Network Device Inspection Tool - Juniper Module

Juniper 장비의 명령어 및 파싱 규칙을 제공합니다.
지원 OS: junos
"""

import logging

logger = logging.getLogger(__name__)

# Juniper 장비 점검 명령어 정의
JUNIPER_INSPECTION_COMMANDS = {
    'juniper': {
        'junos': [
            'show version',
            'show system uptime',
            'show chassis hardware',
            'show configuration'
        ]
    }
}

# Juniper 장비 설정 백업 명령어 정의
JUNIPER_BACKUP_COMMANDS = {
    'juniper': {
        'junos': 'show configuration | display set'
    }
}

# Juniper 장비 출력 파싱 규칙
JUNIPER_PARSING_RULES = {
    'juniper': {
        'junos': {
            'show version': {
                'pattern': r'Junos:\s+([\d\.\-A-Z]+)',
                'output_column': 'Version',
                'first_match_only': True
            },
            'show system uptime': {
                'pattern': r'System booted:.*\(([^)]+)\)',
                'output_column': 'Uptime',
                'first_match_only': True
            },
            'show chassis hardware': {
                'patterns': [
                    {
                        'pattern': r'Chassis\s+(\S+)\s+',
                        'output_column': 'Serial Number',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'^Chassis\s+.*\s(\S+)$',
                        'output_column': 'Model',
                        'first_match_only': True
                    }
                ]
            }
        }
    }
} 