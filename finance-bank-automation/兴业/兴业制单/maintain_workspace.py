"""Command-line wrapper for long-term workspace housekeeping."""

import sys

sys.dont_write_bytecode = True

from cib_open_bank.housekeeping import main


if __name__ == "__main__":
    raise SystemExit(main())
