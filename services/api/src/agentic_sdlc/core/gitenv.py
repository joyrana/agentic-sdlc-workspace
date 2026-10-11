"""One hardened environment for every git process the platform runs.

Git reads "global" configuration from `$HOME/.gitconfig`. Pointing HOME at a repository working
tree (as a naive helper might) would let a committed `.gitconfig` configure filters, pagers or
external diff programs that git then executes on the platform. Here HOME and the global config
are a private, platform-written directory, the system config is ignored, hooks are disabled, and
`safe.directory` is set where git actually honors it (git ignores `-c safe.directory` in the
upload-pack process of a local clone, which breaks clones across the API and worker users).
"""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path

GLOBAL_CONFIG = """\
[safe]
\tdirectory = *
[core]
\thooksPath = /dev/null
\tfsmonitor = false
[protocol "file"]
\tallow = always
[commit]
\tgpgsign = false
"""


@lru_cache(maxsize=1)
def _private_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="asw-git-"))  # 0700, owned by this process's user
    config = home / ".gitconfig"
    config.write_text(GLOBAL_CONFIG)
    os.chmod(config, 0o600)
    return home


def git_env(**extra: str) -> dict[str, str]:
    home = _private_home()
    if not (home / ".gitconfig").is_file():  # tmp cleaners may remove it in long-lived processes
        _private_home.cache_clear()
        home = _private_home()
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        **extra,
    }
