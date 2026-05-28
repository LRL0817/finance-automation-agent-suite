"""
CIB enterprise banking login-only entry point.

Runs USB Hub selection, opens the bank client, completes login, then leaves
the logged-in bank window and UKey connection open.
"""
import sys

sys.dont_write_bytecode = True

from cib_open_bank.main_flow import login_only_main


if __name__ == "__main__":
    login_only_main()
