"""Errors whose text is meant for the user (German, no technical details)."""

from __future__ import annotations


class UserError(RuntimeError):
    """A problem the user can understand and usually fix.

    The message is shown in the interface as it is; everything else is
    logged and replaced by a generic text (see :func:`friendly_message`).
    """


class WorkerCrashed(UserError):
    """A worker process ended while processing a document."""

    def __init__(self) -> None:
        super().__init__(
            "Das Dokument konnte nicht verarbeitet werden – das Programm ist dabei an seine "
            "Grenzen gestoßen (z. B. extrem große Seiten oder zu wenig Arbeitsspeicher).")


class Cancelled(BaseException):
    """The user cancelled the running job.

    Deliberately not an :class:`Exception`: the many "one document must not
    stop the others" handlers must not swallow it.
    """


def friendly_message(exc: BaseException) -> str:
    """Text for the user for any exception (never a traceback or a path)."""
    if isinstance(exc, UserError):
        return str(exc)
    if isinstance(exc, MemoryError):
        return ("Der Arbeitsspeicher reicht für dieses Dokument nicht aus. Bitte andere "
                "Programme schließen oder das PDF in kleinere Teile aufteilen.")
    if isinstance(exc, PermissionError):
        return ("Eine Datei konnte nicht geschrieben werden. Ist das Dokument noch in einem "
                "anderen Programm geöffnet, oder fehlt die Berechtigung für den Ergebnisordner?")
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return "Der Datenträger ist voll. Bitte Speicherplatz freigeben und erneut versuchen."
    return ("Unerwarteter Fehler bei der Verarbeitung. Die technischen Details stehen im "
            f"Protokoll ({exc.__class__.__name__}).")
