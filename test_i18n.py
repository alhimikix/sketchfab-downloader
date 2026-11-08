import ast
from pathlib import Path
import re
from string import Formatter
import unittest

from i18n import CATALOGS, render, set_language, t

ROOT=Path(__file__).parent


class TranslationTests(unittest.TestCase):
    def tearDown(self):set_language('en')

    def test_catalogs_have_identical_keys_and_placeholders(self):
        self.assertEqual(CATALOGS['en'].keys(),CATALOGS['ru'].keys())
        for key in CATALOGS['en']:
            fields=lambda text:{field for _,field,_,_ in Formatter().parse(text) if field is not None}
            self.assertEqual(fields(CATALOGS['en'][key]),fields(CATALOGS['ru'][key]),key)

    def test_progress_key_survives_language_change_and_nested_rendering(self):
        set_language('en')
        message=t('export.note',warning=t('animation.unbound',name='Idle',count=2))
        set_language('ru')
        self.assertEqual(render(message).key,'export.note')
        self.assertIn(CATALOGS['ru']['animation.unbound'].format(name='Idle',count=2),render(message))

    def test_all_literal_translation_calls_reference_catalog_entries(self):
        for path in ROOT.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='t' and node.args:
                    key=node.args[0]
                    if isinstance(key,ast.Constant) and isinstance(key.value,str):
                        self.assertIn(key.value,CATALOGS['en'],str(path)+':'+str(node.lineno))

    def test_no_cyrillic_outside_russian_catalog(self):
        paths=[p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.py','.js','.md','.txt','.spec','.ps1','.cmd')]
        for directory in ('scripts','.github','locales'):
            paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file())
        for path in paths:
            if path==ROOT/'locales'/'ru.json':continue
            self.assertIsNone(re.search(r'[\u0400-\u04ff]',path.read_text(encoding='utf-8')),str(path))


if __name__=='__main__':unittest.main()
