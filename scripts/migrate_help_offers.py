import argparse
import json

from database import engine
from services.help_offers_schema import apply_help_offers_migration, describe_help_offers_migration


def parser():
    result = argparse.ArgumentParser(description="Help Offers schema migration (ofertas directas y responsables)")
    result.add_argument("--apply", action="store_true")
    result.add_argument("--local-test-marker")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.apply:
            report = apply_help_offers_migration(engine, local_test_marker=args.local_test_marker)
        else:
            report = describe_help_offers_migration(engine)
        print(json.dumps(report.public_dict(), sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"error_code": "help_offers_migration_failed", "detail": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
