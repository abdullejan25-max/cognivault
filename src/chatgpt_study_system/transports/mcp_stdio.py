"""Deprecated stdio startup alias; all server implementation lives in cognivault."""

import sys

from cognivault.transports.mcp_stdio import main


if __name__ == "__main__":
    print(
        "chatgpt_study_system.transports.mcp_stdio is deprecated; "
        "use cognivault.transports.mcp_stdio.",
        file=sys.stderr,
    )
    raise SystemExit(main())
