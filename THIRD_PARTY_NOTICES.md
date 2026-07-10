# Third-Party Notices

NetOps Suite is distributed under the MIT License. The project also depends on
third-party packages with their own license terms. Review this file before
publishing source archives or Windows installer builds.

## Direct Runtime Dependencies

| Dependency | License metadata |
| --- | --- |
| PySide6 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| NumPy | BSD-3-Clause |
| pandas | BSD-3-Clause |
| openpyxl | MIT |
| PyYAML | MIT |
| Jinja2 | BSD |
| paramiko | LGPL |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| pyOpenSSL | Apache-2.0 |
| netmiko | MIT |
| telnetlib3 | ISC |
| pyftpdlib | MIT |
| tftpy | MIT |
| msoffcrypto-tool | MIT |
| xlrd | BSD |
| colorama | BSD |
| rich | MIT |
| InquirerPy | MIT |

## Binary Release Notes

- Windows installer builds include a PySide6/Qt runtime through PyInstaller. If
  the LGPL option is used, keep the installed bundle in a form that lets users
  inspect and replace the LGPL-covered Qt libraries.
- Include this file and the project `LICENSE` file with public installer
  artifacts.
- Transitive dependencies are resolved by `requirements.txt` and may add their
  own notice requirements. Re-check dependency metadata before each public
  release.
