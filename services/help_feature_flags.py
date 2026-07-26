import os


class HelpV2FeatureDisabledError(ValueError):
    pass


def is_help_v2_globally_enabled():
    return str(os.getenv("HELP_CASES_V2_ENABLED") or "").strip().lower() == "true"


def enabled_help_v2_organization_ids():
    if not is_help_v2_globally_enabled():
        return frozenset()
    return frozenset(
        organization_id.strip().lower()
        for organization_id in str(os.getenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS") or "").split(",")
        if organization_id.strip()
    )


def is_help_v2_enabled_for_organization(organization_id):
    normalized = str(organization_id or "").strip().lower()
    return bool(normalized) and normalized in enabled_help_v2_organization_ids()


def require_help_v2_organization(organization_id):
    normalized = str(organization_id or "").strip().lower()
    if not is_help_v2_enabled_for_organization(normalized):
        raise HelpV2FeatureDisabledError("Yo te ayudo no esta habilitado para esta organizacion")
    return normalized
