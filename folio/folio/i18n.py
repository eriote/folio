import gettext
import builtins
from pathlib import Path

_LOCALE_DIR = Path(__file__).parent / "locale"
_DOMAIN = "folio"


def setup():
    try:
        t = gettext.translation(_DOMAIN, localedir=str(_LOCALE_DIR))
        builtins._ = t.gettext
        builtins.ngettext = t.ngettext
    except FileNotFoundError:
        builtins._ = lambda s: s
        builtins.ngettext = lambda s, p, n: s if n == 1 else p
