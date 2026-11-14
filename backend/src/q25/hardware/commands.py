"""Capture locale bornée pour les adaptateurs POSIX, sans shell."""

import os
import selectors
import subprocess
import time


class CommandError(RuntimeError):
    """Erreur neutre, sans arguments, chemin ou sortie brute dans le message."""


def bounded_output(argv: tuple[str, ...], *, timeout: float = 2.0,
                   max_bytes: int = 65536) -> str:
    """Exécuter les arguments fixes d'un adaptateur et borner temps et stdout.

    Utilitaire interne POSIX. Les commandes ne proviennent jamais d'un document.
    stderr est abandonné pour éviter de recopier des informations locales.
    """
    if os.name != "posix":
        raise CommandError("capture non disponible sur cette plateforme")
    if timeout <= 0 or type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("Bornes de capture invalides.")
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   shell=False, close_fds=True)
    except OSError:
        raise CommandError("outil inaccessible") from None
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CommandError("délai de sonde dépassé")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, min(4096, max_bytes + 1 - len(output)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > max_bytes:
                            raise CommandError("sortie de sonde trop volumineuse")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommandError("délai de sonde dépassé")
            try:
                code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise CommandError("délai de sonde dépassé") from None
        if code != 0:
            raise CommandError("échec de la sonde")
        try:
            return output.decode("utf-8")
        except UnicodeError:
            raise CommandError("encodage de sonde invalide") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
