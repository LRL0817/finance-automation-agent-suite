"""
CIB enterprise banking automation entry point.

The implementation lives in cib_open_bank; this wrapper keeps the original
run command working:
python open_bank.py
"""
import sys

sys.dont_write_bytecode = True

from cib_open_bank.main_flow import main


if __name__ == "__main__":
    main()
