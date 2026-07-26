import argparse
import getpass
import json
import os

from database import DATABASE_URL
from services.help_backup import restore_postgres_backup


def parser():
    result = argparse.ArgumentParser(description="Verified local Help V2 restore")
    result.add_argument("--artifact", required=True)
    result.add_argument("--destination-root", required=True)
    result.add_argument("--pg-restore-bin", default="pg_restore")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = restore_postgres_backup(
            database_url=DATABASE_URL,
            destination_root=args.destination_root,
            artifact_path=args.artifact,
            passphrase=os.getenv("HELP_BACKUP_PASSPHRASE") or getpass.getpass("Backup passphrase: "),
            pg_restore_bin=args.pg_restore_bin,
        )
        print(json.dumps({"status": "restored", **result}, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        print('{"error_code":"restore_failed","status":"failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
