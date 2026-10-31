"""Explicit operator-selected mapping of an existing local account to Clerk."""
import argparse
import sys

from sqlalchemy import select

from . import auth, config
from .db import session_scope
from .models import User


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicitly link one existing yourads account to one Clerk user.")
    parser.add_argument("--local-user-id", required=True, type=int)
    parser.add_argument("--clerk-user-id", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="validate the selected pair without writing")
    mode.add_argument("--apply", action="store_true", help="write the explicitly selected mapping")
    args = parser.parse_args(argv)
    try:
        config.validate_clerk_config()
        email, _ = auth._profile(auth._provider_user(args.clerk_user_id))
        with session_scope() as session:
            row = session.execute(select(User).where(User.id == args.local_user_id).with_for_update()).scalar_one_or_none()
            if row is None:
                raise RuntimeError("selected local account does not exist")
            subject = session.scalar(select(User.id).where(User.clerk_user_id == args.clerk_user_id).limit(1))
            if subject not in (None, row.id):
                raise RuntimeError("Clerk user is already mapped to another local account")
            if row.clerk_user_id not in (None, args.clerk_user_id):
                raise RuntimeError("local account is already mapped to another Clerk user")
            if row.email.strip().lower() != email:
                raise RuntimeError("verified Clerk primary email does not match the selected local account")
            if args.apply:
                row.clerk_user_id = args.clerk_user_id
        print("mapping validated" if args.check else "mapping applied")
        return 0
    except Exception as exc:
        print(f"link stopped: {exc if isinstance(exc, RuntimeError) else 'provider or database operation failed'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
