def format_de_number(
    value: object,
    decimals: int = 2,
    *,
    signed: bool = False,
    significant: bool = False,
) -> str:
    precision = f".{decimals}{'g' if significant else 'f'}"
    format_spec = f"{'+' if signed else ''},{precision}"
    formatted = format(value, format_spec)
    return formatted.replace(",", "\0").replace(".", ",").replace("\0", ".")
