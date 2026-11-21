"""Dedicated frozen app-server entry point used by platform VSIX builds."""

import sys

from open_code_agent import __version__


def main() -> None:
    if sys.argv[1:] == ["--runtime-info"]:
        print(f"open-code-agent {__version__}")
        return
    if sys.argv[1:] not in ([], ["app-server"]):
        print("This bundled executable supports only app-server mode.", file=sys.stderr)
        raise SystemExit(2)
    from open_code_agent.cli import app

    app(args=["app-server"], standalone_mode=True)


if __name__ == "__main__":
    main()
