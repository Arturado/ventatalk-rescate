import pytest

from services.help_identity import HelpIdentityError, normalize_venezuelan_identity


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("11111111", "V-11111111"),
        ("11.111.111", "V-11111111"),
        (" V11111111 ", "V-11111111"),
        ("v-11.111.111", "V-11111111"),
        ("E 12 345 678", "E-12345678"),
        ("V-000001", "V-000001"),
    ],
)
def test_normalizes_supported_venezuelan_identity_formats(raw_value, expected):
    assert normalize_venezuelan_identity(raw_value) == expected


@pytest.mark.parametrize(
    "raw_value",
    [
        "",
        "V-12345",
        "V-1234567890",
        "A-12345678",
        "VE-12345678",
        "V-12/345/678",
        "V-12_345_678",
        "V-12X345678",
        "1234-ABCD",
    ],
)
def test_rejects_unsupported_identity_prefixes_symbols_and_lengths(raw_value):
    with pytest.raises(HelpIdentityError):
        normalize_venezuelan_identity(raw_value)


def test_keeps_venezuelan_and_foreign_prefixes_distinct():
    assert normalize_venezuelan_identity("V-11111111") != normalize_venezuelan_identity("E-11111111")
