"""Manage operator accounts.

    python -m robotweb.manage_users list
    python -m robotweb.manage_users add NAME
    python -m robotweb.manage_users set-password NAME
    python -m robotweb.manage_users remove NAME

Passwords are asked for at the prompt and never shown or passed on the command
line. Changes take effect without restarting the backend. Removing an account
ends its sessions; changing a password does not end sessions that are already
logged in.
"""

import argparse
import getpass
import sys

from .auth import UserError, UserStore
from .config import get_settings


def _ask_password() -> str:
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise UserError("the two passwords differ")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m robotweb.manage_users", description="Manage operator accounts")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list the accounts")
    for name, text in (
        ("add", "create an account"),
        ("set-password", "change an account's password"),
        ("remove", "delete an account"),
    ):
        commands.add_parser(name, help=text).add_argument("username")
    args = parser.parse_args(argv)

    path = get_settings().users_file
    users = UserStore(path)
    try:
        if args.command == "list":
            names = users.names()
            print("\n".join(names) if names else f"no accounts in {path}")
        elif args.command == "add":
            users.set_password(args.username, _ask_password(), create=True)
            print(f"created operator {args.username!r} in {path}")
        elif args.command == "set-password":
            users.set_password(args.username, _ask_password(), create=False)
            print(f"password changed for {args.username!r}")
        elif args.command == "remove":
            users.remove(args.username)
            print(f"removed {args.username!r}")
    except UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
