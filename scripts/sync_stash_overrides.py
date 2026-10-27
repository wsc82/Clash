#!/usr/bin/env python3
"""Regenerate Stash overrides, mirror their scripts, and record failover.

Run from anywhere. Paths are relative to the repository root. Exit 0 when
every upstream accepted the update. Exit 2 when at least one upstream was
rejected and the last good copy was kept. Exit 1 on a local error.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from qx_to_stash import self_check as qx_self_check  # noqa: E402
from stash_mirror import mirror_self_check, sync_sources  # noqa: E402
from stash_sources import SOURCES  # noqa: E402
from surge_module_to_stash import ConvertError, self_check  # noqa: E402


def main() -> int:
    try:
        self_check()
        qx_self_check()
        check_log = io.StringIO()
        try:
            with contextlib.redirect_stdout(check_log):
                mirror_self_check()
        except Exception:
            sys.stderr.write(check_log.getvalue())
            raise
        if "FAILOVER Stash/Example.stoverride demo.js: http-404" not in check_log.getvalue():
            raise ConvertError("mirror self-check did not log a 404 failover")
        return sync_sources(ROOT, SOURCES)
    except ConvertError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
