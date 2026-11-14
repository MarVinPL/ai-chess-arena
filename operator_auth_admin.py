#!/usr/bin/env python3
"""Local one-use bootstrap for the first AI Chess Arena owner Passkey.

Run only from the trusted VPS shell as the Arena operator. The generated token
is printed once and must be entered into the configured HTTPS Arena origin.
"""
import argparse
from pathlib import Path

import operator_auth


def main():
    parser = argparse.ArgumentParser(description="AI Chess Arena operator bootstrap")
    sub = parser.add_subparsers(dest="command", required=True)
    owner = sub.add_parser("create-owner", help="create one short-lived first-owner enrollment token")
    owner.add_argument("--display-name", required=True)
    owner.add_argument("--ttl", type=int, default=operator_auth.TOKEN_TTL_SECONDS)
    args = parser.parse_args()
    if args.command == "create-owner":
        db_path = Path(__file__).resolve().parent / "arena.sqlite3"
        token = operator_auth.create_owner_enrollment(db_path, args.display_name, args.ttl)
        print("Jednorazowy token rejestracji właściciela (wyświetlany tylko teraz):")
        print(token)
        print("Ważność: {} sekund. Nie zapisuj tokenu w repozytorium ani logach.".format(args.ttl))


if __name__ == "__main__":
    main()
