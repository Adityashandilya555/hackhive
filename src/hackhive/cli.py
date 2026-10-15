"""The `hackhive` command. Subcommands arrive with #16 (solve, check) and #32 (batch)."""

import sys


def main(argv: list[str] | None = None) -> int:
    print("hackhive: no commands yet (solve/check land in #16, batch in #32)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
