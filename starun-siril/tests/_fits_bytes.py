"""Byte-exact FITS card/HDU builders shared by container, sampling and delivery tests.

Test-only helper module: leading underscore => never collected by pytest,
and tests/ is outside release-files.txt so never shipped. Zero imports by design.
"""


def _fits_card(keyword: str, value: object | None = None) -> bytes:
    if value is None:
        text = keyword
    else:
        if isinstance(value, bool):
            encoded = "T" if value else "F"
        elif isinstance(value, str):
            encoded = f"'{value}'"
        else:
            encoded = str(value)
        text = f"{keyword:<8}= {encoded:>20}"
    return text.ljust(80).encode("ascii")


def _fits_hdu(cards: list[tuple[str, object]], data: bytes = b"") -> bytes:
    header = b"".join(_fits_card(key, value) for key, value in cards)
    header += _fits_card("END")
    header += b" " * (-len(header) % 2880)
    return header + data + b"\0" * (-len(data) % 2880)
