"""Operator tool: set a new password for an account (there is no e-mail reset flow).

    python -m scripts.reset_password someone@example.com

The new password is read from a hidden prompt. All of the account's existing sessions are
signed out. Verify the person's identity yourself before running this.
"""

import getpass
import sys

from app import auth


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m scripts.reset_password <email>")
    pw = getpass.getpass("New password: ")
    if pw != getpass.getpass("Repeat: "):
        raise SystemExit("Passwords do not match.")
    try:
        auth.admin_set_password(sys.argv[1], pw)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print("Password updated; existing sessions are now invalid.")


if __name__ == "__main__":
    main()
