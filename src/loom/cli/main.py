"""M0 stub — only the `serve` verb (BUILD-SPEC §9.1).

M3 fills in `init`, `index`, `ls`, `show`, `release`. Boot-time indexing (`serve` indexes
if the node table is empty) lands with M1's `indexer.walk`; the M0 skeleton serves without it.
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    ap = argparse.ArgumentParser(prog="loom", description="loom — spec-driven coordination gate")
    sub = ap.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="run the loom MCP + /gate server")
    p_serve.add_argument("--repo-root", required=True)
    p_serve.add_argument("--repo", default="")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8790)
    p_serve.add_argument("--db", default="")

    args = ap.parse_args()
    if args.cmd != "serve":
        ap.print_help(sys.stderr)
        raise SystemExit(2)

    from loom.server.app import serve

    repo_root = os.path.abspath(args.repo_root)
    # The repo salt is minted once, at serve, and echoed to every `loom init` (§11.19).
    repo = args.repo or os.path.basename(repo_root.rstrip("/")) or "repo"
    db_path = args.db or os.path.join(repo_root, ".loom.sqlite3")
    serve(args.host, args.port, db_path, repo, repo_root)


if __name__ == "__main__":
    main()
