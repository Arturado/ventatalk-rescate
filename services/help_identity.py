import re


class HelpIdentityError(ValueError):
    pass


def normalize_venezuelan_identity(value):
    raw_value = str(value or "").strip().upper()
    if not raw_value or not re.fullmatch(r"[VE0-9. -]+", raw_value):
        raise HelpIdentityError("La cedula solo admite prefijo V/E, digitos y separadores")

    prefix = raw_value[0] if raw_value[0] in {"V", "E"} else "V"
    identity_digits = raw_value[1:] if raw_value[0] in {"V", "E"} else raw_value
    digits = re.sub(r"[. -]", "", identity_digits)
    if not re.fullmatch(r"[0-9]{6,9}", digits):
        raise HelpIdentityError("La cedula debe contener entre 6 y 9 digitos")

    return f"{prefix}-{digits}"
