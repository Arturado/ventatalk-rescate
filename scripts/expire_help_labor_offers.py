"""Expire due labor offers. Intended for an authorized isolated worker."""

import argparse

from database import SessionLocal
from services.help_labor_access import expire_due_labor_offers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply expiration transitions")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("--apply is required; no changes were made")
    with SessionLocal() as db:
        result = expire_due_labor_offers(db, worker_authorized=True, limit=args.limit)
        db.commit()
        print({"selected": result["selected"], "expired": result["expired"]})


if __name__ == "__main__":
    main()
