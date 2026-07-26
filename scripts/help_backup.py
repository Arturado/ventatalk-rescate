import argparse
import getpass
import json
import os

from database import DATABASE_URL
from services.help_backup import create_postgres_backup


def _passphrase():
    value = os.getenv("HELP_BACKUP_PASSPHRASE")
    if value:
        return value
    first = getpass.getpass("Backup passphrase: ")
    second = getpass.getpass("Confirm backup passphrase: ")
    if first != second:
        raise ValueError("Passphrases differ")
    return first


def parser():
    result = argparse.ArgumentParser(description="Encrypted local Help V2 backup")
    result.add_argument("--output", required=True)
    result.add_argument("--upload-root")
    result.add_argument("--firestore-emulator-root")
    result.add_argument("--pg-dump-bin", default="pg_dump")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    versions = [
        name.removeprefix("HELP_DATA_ENCRYPTION_KEY_").lower()
        for name, value in os.environ.items()
        if name.startswith("HELP_DATA_ENCRYPTION_KEY_") and value
    ]
    try:
        result = create_postgres_backup(
            database_url=DATABASE_URL,
            upload_root=args.upload_root or os.getenv("HELP_PRIVATE_UPLOAD_ROOT", "uploads"),
            output_path=args.output,
            passphrase=_passphrase(),
            key_versions=versions,
            schema_identifier="help-v2-retention-v1",
            firestore_emulator_root=args.firestore_emulator_root,
            pg_dump_bin=args.pg_dump_bin,
        )
        print(json.dumps({"status": "created", **result}, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        print('{"error_code":"backup_failed","status":"failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
