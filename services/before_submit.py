import json
from datetime import datetime, timezone
from typing import Any, Optional


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_items(value: Any) -> Optional[str]:
    if value is None:
        return None

    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise ValueError("before_submit_items debe ser JSON válido.") from exc

    if not isinstance(parsed, list):
        raise ValueError("before_submit_items debe ser un arreglo JSON.")

    items = []
    for item in parsed:
        if not isinstance(item, dict):
            continue

        key = normalize_optional_text(item.get("key"))
        label = normalize_optional_text(item.get("label"))
        if not key or not label:
            continue

        items.append({"key": key, "label": label})

    return json.dumps(items, ensure_ascii=False) if items else None


def _normalize_confirmations(value: Any) -> Optional[str]:
    if value is None:
        return None

    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise ValueError("before_submit_confirmations debe ser JSON válido.") from exc

    if not isinstance(parsed, dict):
        raise ValueError("before_submit_confirmations debe ser un objeto JSON.")

    confirmations = {}
    for key, item_value in parsed.items():
        normalized_key = normalize_optional_text(key)
        if not normalized_key:
            continue
        confirmations[normalized_key] = bool(item_value)

    return json.dumps(confirmations, ensure_ascii=False) if confirmations else None


def build_before_submit_metadata(
    *,
    version: Optional[str],
    title: Optional[str],
    description: Optional[str],
    items: Any,
    confirmations: Any,
    source: Optional[str],
):
    normalized_version = normalize_optional_text(version)
    normalized_title = normalize_optional_text(title)
    normalized_description = normalize_optional_text(description)
    normalized_items = _normalize_items(items)
    normalized_confirmations = _normalize_confirmations(confirmations)
    normalized_source = normalize_optional_text(source)

    has_data = any(
        [
            normalized_version,
            normalized_title,
            normalized_description,
            normalized_items,
            normalized_confirmations,
        ]
    )

    if not has_data:
        return {
            "before_submit_version": None,
            "before_submit_title": None,
            "before_submit_description": None,
            "before_submit_items_json": None,
            "before_submit_confirmations_json": None,
            "before_submit_acknowledged_at": None,
            "before_submit_source": None,
        }

    return {
        "before_submit_version": normalized_version or "shelter-request-v1",
        "before_submit_title": normalized_title,
        "before_submit_description": normalized_description,
        "before_submit_items_json": normalized_items,
        "before_submit_confirmations_json": normalized_confirmations,
        "before_submit_acknowledged_at": datetime.now(timezone.utc).isoformat(),
        "before_submit_source": normalized_source,
    }
