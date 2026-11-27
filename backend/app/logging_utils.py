"""Small helpers shared across backend modules."""


def clean_for_log(value: object) -> str:
    """Return a log-safe rendering of untrusted values.

    Feed and article values (titles, URLs, error text) are logged verbatim in
    several places. Stripping CR/LF (and other ASCII control characters that
    terminals act on) prevents untrusted content from forging additional log
    lines or ANSI escape sequences.
    """
    text = str(value)
    return "".join(" " if ord(char) < 32 else char for char in text)
