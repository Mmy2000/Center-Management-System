"""Extract translatable strings without GNU gettext (TASK-082 support).

`makemessages` needs the xgettext binary, which is not available on a plain
Windows box. This command does the same job for the patterns this project
actually uses:

    {% translate "…" %} / {% trans "…" %}    templates
    _("…") / gettext("…") / gettext_lazy("…")  Python
    gettext("…")                              JavaScript (static/js + inline)

It writes locale/<lang>/LC_MESSAGES/django.po, preserving existing
translations, and compiles the .mo with polib — also pure Python.
"""

import pathlib
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

TEMPLATE_TAG = re.compile(
    r"""\{%\s*(?:translate|trans)\s+(?P<q>["'])(?P<text>(?:\\.|(?!(?P=q)).)*)(?P=q)"""
)
PY_CALL = re.compile(
    r"""(?<![\w.])(?:_|gettext|gettext_lazy|ngettext)\(\s*(?P<q>["'])(?P<text>(?:\\.|(?!(?P=q))[^\\\r\n])*)(?P=q)"""
)
JS_CALL = re.compile(
    r"""(?<![\w.])gettext\(\s*(?P<q>["'])(?P<text>(?:\\.|(?!(?P=q))[^\\\r\n])*)(?P=q)"""
)

SOURCES = (
    ("templates", "*.html", (TEMPLATE_TAG, JS_CALL)),
    ("apps", "*.py", (PY_CALL,)),
    ("static/js", "*.js", (JS_CALL,)),
)
SKIP_PARTS = {"migrations", "tests", "__pycache__", "venv", "node_modules"}


def unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n")


class Command(BaseCommand):
    help = "Extract translatable strings into locale/<lang>/LC_MESSAGES/django.po"

    def add_arguments(self, parser):
        parser.add_argument("--locale", "-l", default="en")
        parser.add_argument("--compile", action="store_true", help="Also write the .mo")
        parser.add_argument(
            "--check", action="store_true", help="Report untranslated entries and exit 1"
        )

    def handle(self, *args, **options):
        try:
            import polib
        except ImportError as exc:  # pragma: no cover
            raise CommandError("polib is required: pip install polib") from exc

        base = pathlib.Path(settings.BASE_DIR)
        found: dict[str, list[str]] = {}

        for folder, glob, patterns in SOURCES:
            for path in sorted((base / folder).rglob(glob)):
                if SKIP_PARTS & set(path.parts):
                    continue
                text = path.read_text(encoding="utf-8")
                for pattern in patterns:
                    for match in pattern.finditer(text):
                        msgid = unescape(match.group("text")).strip()
                        if not msgid:
                            continue
                        line = text.count("\n", 0, match.start()) + 1
                        reference = f"{path.relative_to(base).as_posix()}:{line}"
                        found.setdefault(msgid, []).append(reference)

        locale = options["locale"]
        po_path = base / "locale" / locale / "LC_MESSAGES" / "django.po"
        po_path.parent.mkdir(parents=True, exist_ok=True)

        existing = {}
        if po_path.exists():
            for entry in polib.pofile(str(po_path)):
                if entry.msgstr:
                    existing[entry.msgid] = entry.msgstr

        catalog = polib.POFile(check_for_duplicates=False)
        catalog.metadata = {
            "Project-Id-Version": "Center Management System",
            "Language": locale,
            "MIME-Version": "1.0",
            "Content-Type": "text/plain; charset=UTF-8",
            "Content-Transfer-Encoding": "8bit",
            "Plural-Forms": "nplurals=2; plural=(n != 1);",
        }
        for msgid in sorted(found):
            catalog.append(
                polib.POEntry(
                    msgid=msgid,
                    msgstr=existing.get(msgid, ""),
                    occurrences=[
                        (ref.rsplit(":", 1)[0], ref.rsplit(":", 1)[1]) for ref in found[msgid][:6]
                    ],
                )
            )
        catalog.save(str(po_path))

        translated = sum(1 for entry in catalog if entry.msgstr)
        missing = [entry.msgid for entry in catalog if not entry.msgstr]

        if options["compile"]:
            catalog.save_as_mofile(str(po_path.with_suffix(".mo")))

        verbosity = options.get("verbosity", 1)
        # A Windows console is cp1252: printing Arabic msgids raw would crash the
        # command with UnicodeEncodeError.
        encoding = getattr(self.stdout, "encoding", None) or "utf-8"

        def safe(text: str) -> str:
            return text.encode(encoding, errors="replace").decode(encoding)

        if verbosity >= 1:
            self.stdout.write(
                f"{locale}: {len(catalog)} strings, {translated} translated, {len(missing)} missing"
            )
            for msgid in missing[:15]:
                self.stdout.write(safe(f"  - {msgid[:70]}"))

        if options["check"] and missing:
            raise CommandError(f"{len(missing)} untranslated strings in {locale}")
