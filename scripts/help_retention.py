import argparse
import json
import os
from datetime import datetime

from database import SessionLocal
from services.help_retention import run_retention


def parser():
    result = argparse.ArgumentParser(description="Local Help V2 retention runner")
    result.add_argument("--apply", action="store_true")
    result.add_argument("--local-test-marker")
    result.add_argument("--upload-root")
    result.add_argument("--now", help="Timezone-aware ISO-8601 clock override")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else None
    try:
        with SessionLocal() as db:
            report = run_retention(
                db,
                upload_root=args.upload_root or os.getenv("HELP_PRIVATE_UPLOAD_ROOT", "uploads"),
                now=now,
                apply=args.apply,
                local_test_marker=args.local_test_marker,
            )
        print(json.dumps(report.public_dict(), sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        print('{"error_code":"retention_failed","status":"failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
