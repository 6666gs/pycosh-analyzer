# vendor/

Third-party code bundled directly into the repo. Right now that's just:

- `pycosh/` — vendored as-is (reference implementation, no local changes)

The app prepends `vendor/` to `sys.path` at import time, so `import pycosh`
resolves to the copy here.

> The **SDS7404A driver is no longer vendored here.** It lives in its own repo
> and is pulled in via pip (`sds7404 @ git+https://github.com/6666gs/sds7404.git`
> in `requirements.txt`), so `import sds7404` resolves to the installed package.
> For local driver development without reinstalling, point
> `$DBPD_SDS7404_PARENT` at the folder holding `sds7404.py`.

## `vendor/pycosh/`

Reference implementation of the correlated self-heterodyne (COSH) analysis
from Yuan, Wang, Liu, et al. *Opt. Express* **30**, 25147 (2022).

- **Upstream**: original release by **Maodong Gao** (2022)
- **License**: MIT — preserved verbatim in `vendor/pycosh/LICENSE`
- **Files**: `CoshConfig.py`, `CoshXcorr.py`, `__init__.py`
- **No local modifications** — vendored as-is to avoid drift from upstream

If upstream releases a meaningful update, replace these files and bump the
provenance note here.
