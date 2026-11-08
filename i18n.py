"""Small shared message catalog for the desktop app, CLI and exporter."""
import json
import locale
import os
from pathlib import Path

LANGUAGES = ('en', 'ru')
CATALOGS = {code: json.loads((Path(__file__).parent / 'locales' / (code + '.json')).read_text(encoding='utf-8'))
            for code in LANGUAGES}


def system_language():
    name = os.environ.get('SKETCHFAB_LOCAL_LANGUAGE') or locale.getlocale()[0] or 'en'
    return 'ru' if name.lower().startswith('ru') else 'en'


_language = system_language()


class Message(str):
    """Keep a stable event key alongside the rendered log text."""
    def __new__(cls, key, parameters):
        values = {name: render(value) if isinstance(value, Message) else value for name, value in parameters.items()}
        obj = super().__new__(cls, CATALOGS[_language][key].format(**values))
        obj.key, obj.parameters = key, parameters
        return obj


def set_language(code):
    global _language
    if code not in LANGUAGES:
        raise ValueError('Unsupported language: ' + str(code))
    _language = code


def get_language():
    return _language


def t(key, **parameters):
    return Message(key, parameters)


def render(value):
    return t(value.key, **value.parameters) if isinstance(value, Message) else str(value)
