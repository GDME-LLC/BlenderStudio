## [1.2.5] - 2026-11-20
### Orca Cloud Plugin Hub Certification & Upfront Security Permissions

#### Added & Enhanced
- Declared upfront capabilities via `orca.request_permissions(...)` for a single combined approval dialog on load (`network_socket`, `network_http`, `fs_read`, `fs_readwrite`, `process`, `threading`).
- 100% Self-Contained Plugin Hub installation: Added **📋 Copy Bootstrap Script** and **💾 Export server_bootstrap.py** buttons inside Settings modal.
- Automated GitHub Actions release pipeline triggering on push to `main` with multi-OS distribution assets.
- Automatic Changelog generation integrated into build and release pipelines.

#### Fixed & Security
- Sanitized child process environments with `_ALLOWED_CHILD_ENV_KEYS`, eliminating sensitive `os.environ` credential leakage.
- Hardened outbound network requests with `_validate_safe_url` and `_safe_urlopen` restricting calls strictly to HTTP/HTTPS schemes with host validation.
- Resolved duplicate target conflict in Orca Cloud Plugin Hub releases.
