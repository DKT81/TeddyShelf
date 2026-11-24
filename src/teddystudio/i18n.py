"""JSON translations with canonical source values and safe named placeholders.

Text objects remain ordinary German source strings for comparisons, snapshots and
JSON persistence. Only the presentation layer renders them in the selected language.
"""

from pathlib import Path
import json
import logging
import os
import re
import string
import sys
import weakref

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

LOG = logging.getLogger(__name__)
FORMATTER = string.Formatter()


def fields(value):
    try:
        return {name for _, name, _, _ in FORMATTER.parse(value) if name is not None}
    except ValueError:
        return set()


def validate_catalog(data):
    if not isinstance(data, dict):
        raise ValueError("Translation catalog must be a JSON object")
    if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)*", data.get("code", "")):
        raise ValueError("Invalid translation language code")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ValueError("Missing native language name")
    if not isinstance(data.get("messages"), dict):
        raise ValueError("Missing messages dictionary")
    qt = data.get("qt", {})
    if not isinstance(qt, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value
        for key, value in qt.items()
    ):
        raise ValueError("Qt translations must be a dictionary of nonempty strings")
    for source, target in data["messages"].items():
        if not isinstance(source, str) or not isinstance(target, str) or not target:
            raise ValueError("Translations must be nonempty strings")
        if fields(source) != fields(target):
            raise ValueError("Translation placeholders differ: " + source)
    return data


class Text(str):
    def __new__(cls, source, parameters=None, parts=None):
        canonical = (
            "".join(map(str, parts))
            if parts is not None
            else (source.format(**parameters) if parameters is not None else source)
        )
        instance = super().__new__(cls, canonical)
        instance.source, instance.parameters, instance.parts = source, parameters, parts
        return instance

    def translated(self):
        if self.parts is not None:
            return "".join(render(p) for p in self.parts)
        target = manager.lookup(self.source)
        if self.parameters is None:
            return target
        try:
            return target.format(
                **{
                    k: render(v) if isinstance(v, Text) else v
                    for k, v in self.parameters.items()
                }
            )
        except (ValueError, KeyError, TypeError):
            LOG.warning("Invalid translation format for %r", self.source)
            return str(self)

    def __add__(self, value):
        return (
            Text("", parts=[self, value]) if isinstance(value, str) else NotImplemented
        )

    def __radd__(self, value):
        return (
            Text("", parts=[value, self]) if isinstance(value, str) else NotImplemented
        )

    def join(self, values):
        parts = []
        for value in values:
            if parts:
                parts.append(self)
            parts.append(value)
        return Text("", parts=parts)

    def replace(self, old, new, count=-1):
        return text(str(self).replace(old, new, count))


def text(source):
    return source if isinstance(source, Text) else Text(str(source))


def format_text(source, **parameters):
    return Text(source, parameters=parameters)


def render(value):
    if isinstance(value, Text):
        return value.translated()
    if isinstance(value, list):
        return [render(v) for v in value]
    if isinstance(value, tuple):
        return tuple(render(v) for v in value)
    return value


class JsonQtTranslator(QTranslator):
    def isEmpty(self):
        # Qt otherwise considers a Python-backed translator without a .qm empty.
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        # None produces a null QString, allowing Qt's next translator to run.
        # An empty Python string would instead hide the standard button label.
        return manager.catalogs[manager.language].get("qt", {}).get(sourceText)


class TranslationManager:
    def __init__(self):
        self.language = "de"
        self.catalogs = {}
        self.bindings = {}
        self.listeners = []
        self.qt_translator = None
        self.qt_base = None
        self._patterns = {}
        self.reload()

    def reload(self, extra_paths=()):
        directories = [Path(__file__).with_name("locales")]
        if getattr(sys, "frozen", False):
            directories.append(Path(sys.executable).parent / "translations")
        configured = os.environ.get("TEDDYSHELF_DATA") or os.environ.get(
            "TEDDYSTUDIO_DATA"
        )
        base = (
            Path(configured)
            if configured
            else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TeddyShelf"
        )
        if not configured:
            legacy = base.parent / "TeddyStudio"
            has_data = lambda directory: any(
                (directory / name).is_file()
                for name in ("settings.json", "library.sqlite")
            )
            if not has_data(base) and has_data(legacy):
                base = legacy
        directories.append(base / "translations")
        directories.extend(map(Path, extra_paths))
        catalogs = {}
        for folder in directories:
            for path in sorted(folder.glob("*.json")):
                try:
                    if path.stat().st_size > 2_000_000:
                        raise ValueError("Translation file too large")
                    data = validate_catalog(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                    catalogs[data["code"]] = data
                except (OSError, ValueError, TypeError) as exc:
                    LOG.warning("Ignoring translation file %s: %s", path.name, exc)
        if "de" not in catalogs or "en" not in catalogs:
            raise RuntimeError("German and English translation files are required")
        self.catalogs = catalogs
        self._patterns.clear()

    def normalize(self, language):
        code = {"Deutsch": "de", "English": "en", "Englisch": "en"}.get(
            language, language
        )
        return code if code in self.catalogs else "de"

    def available(self):
        return [(code, data["name"]) for code, data in sorted(self.catalogs.items())]

    def lookup(self, source):
        direct = self.catalogs[self.language]["messages"].get(source)
        if direct is not None:
            return direct
        # Worker errors/logs have already been formatted before reaching the GUI.
        # Match only explicitly catalogued message templates; captured user data is
        # substituted verbatim rather than sent to a translation service.
        if self.language == "de":
            return source
        patterns = self._patterns.get(self.language)
        if patterns is None:
            patterns = []
            for original, target in self.catalogs[self.language]["messages"].items():
                if not re.search(r"\{p\d+", original):
                    continue
                pieces, names = [], []
                for literal, name, spec, conversion in FORMATTER.parse(original):
                    pieces.append(re.escape(literal))
                    if name is not None:
                        if name in names:
                            pieces.append("(?P=" + name + ")")
                        else:
                            pieces.append("(?P<" + name + ">.+?)")
                            names.append(name)
                try:
                    patterns.append(
                        (re.compile("^" + "".join(pieces) + "$", re.S), target)
                    )
                except re.error:
                    continue
            self._patterns[self.language] = patterns
        for pattern, target in patterns:
            match = pattern.fullmatch(source)
            if match:
                return re.sub(
                    r"\{(p\d+)(?:![ars])?(?::[^{}]*)?\}",
                    lambda m: match.group(m.group(1)),
                    target,
                )
        return source

    def register(self, target, callback):
        identifier = id(target)
        self.bindings[identifier] = (
            weakref.ref(target, lambda _: self.bindings.pop(identifier, None)),
            callback,
        )

    def listen(self, owner, callback_name):
        self.listeners.append((weakref.ref(owner), callback_name))

    def set_language(self, language):
        self.language = self.normalize(language)
        app = QCoreApplication.instance()
        if app:
            if self.qt_translator:
                app.removeTranslator(self.qt_translator)
            if self.qt_base:
                app.removeTranslator(self.qt_base)
            import PySide6

            self.qt_base = QTranslator()
            # Qt's wheel layout differs between Windows and Linux. Frozen
            # Windows builds also carry an explicit bundled catalog fallback.
            for path in (
                QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
                str(Path(PySide6.__file__).parent / "translations"),
            ):
                if self.qt_base.load("qtbase_" + self.language, path):
                    app.installTranslator(self.qt_base)
                    break
            self.qt_translator = JsonQtTranslator()
            if self.catalogs[self.language].get("qt"):
                app.installTranslator(self.qt_translator)
            QLocale.setDefault(QLocale(self.language))
            if hasattr(app, "setLayoutDirection"):
                app.setLayoutDirection(QLocale(self.language).textDirection())
        for reference, callback in list(self.bindings.values()):
            target = reference()
            if target is not None:
                try:
                    callback(target)
                except RuntimeError:
                    self.bindings.pop(id(target), None)
        living = []
        for reference, name in self.listeners:
            owner = reference()
            if owner is not None:
                try:
                    getattr(owner, name)()
                    living.append((reference, name))
                except RuntimeError:
                    # A Python wrapper may outlive its deleted Qt window.
                    continue
        self.listeners = living


manager = TranslationManager()
