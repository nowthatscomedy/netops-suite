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
| rich | MIT |
| Pygments | BSD-2-Clause |

## Legacy SSH Compatibility Component

The Inspector SSH compatibility helper uses Paramiko 3.5.1 (LGPL-2.1-or-later),
whose hash is pinned in `requirements-legacy-ssh-lock.txt`. The Windows installer
and portable zip ship it unmodified in `_internal\legacy_ssh`, with its license
in the bundled `paramiko-3.5.1.dist-info` folder. It runs only in a separate
worker process for devices that offer nothing but SHA-1 or DSS SSH algorithms;
the application itself uses Paramiko 5. Its other dependencies are the same
pinned packages listed above. Development checkouts install it into an isolated
environment with `scripts/setup_legacy_ssh.py`.

Paramiko 3.5.1 is listed as PYSEC-2026-2858 because it accepts SHA-1 RSA
signatures. Accepting those algorithms is the reason this component exists, so
the advisory is expected for it and does not apply to the main application.

## Binary Release Notes

- The offline desktop UI includes original Lucide SVG icons (ISC; selected
  Feather-derived icons under MIT). Source revision and full notices are in
  `assets/icons/lucide/SOURCE.md` and `assets/icons/lucide/LICENSE`.

- Windows installer builds include a PySide6/Qt runtime through PyInstaller. If
  the LGPL option is used, keep the installed bundle in a form that lets users
  inspect and replace the LGPL-covered Qt libraries.
- Include this file and the project `LICENSE` file with public installer
  artifacts.
- Transitive dependencies are resolved by `requirements.txt` and may add their
  own notice requirements. Re-check dependency metadata before each public
  release.
