import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import engine
from services.help_crypto import HelpDataCipher
from services.help_labor_access_schema import (
    apply_labor_access_migration,
    describe_labor_access_migration,
    rollback_labor_access_migration,
)


def parser():
    result = argparse.ArgumentParser(description="Protected labor-offer access schema migration")
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    result.add_argument("--backup-reference")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.apply:
            cipher = HelpDataCipher.from_environment()
            report = apply_labor_access_migration(
                engine,
                email_encryptor=lambda value: cipher.encrypt(value, field="offer.actor_email"),
                email_decryptor=lambda value: cipher.decrypt(value, field="offer.actor_email"),
                backup_reference=args.backup_reference,
            )
        elif args.rollback:
            cipher = HelpDataCipher.from_environment()
            report = rollback_labor_access_migration(
                engine,
                email_decryptor=lambda value: cipher.decrypt(value, field="offer.actor_email"),
                backup_reference=args.backup_reference,
            )
        else:
            report = describe_labor_access_migration(engine)
        print(json.dumps(report.public_dict(), sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({
            "error_code": "help_labor_access_migration_failed",
            "detail": str(exc),
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
