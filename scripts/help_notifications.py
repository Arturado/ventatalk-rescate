import argparse
import json

from database import SessionLocal
from services.help_notifications import ProviderConfigurationError, process_notification_batch


def main():
    parser = argparse.ArgumentParser(description="Procesa un lote acotado de notificaciones de ayuda")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            result = process_notification_batch(db, limit=max(1, min(args.limit, 100)))
        except ProviderConfigurationError as exc:
            parser.exit(2, f"Notificaciones no configuradas: {exc}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
