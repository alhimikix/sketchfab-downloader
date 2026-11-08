import os
from pathlib import Path
import unittest
from unittest.mock import patch

import platform_support as support


class PlatformTests(unittest.TestCase):
    def test_external_blender_uses_original_library_search_path(self):
        with patch.object(support.sys,'frozen',True,create=True), patch.dict(os.environ,{
                'LD_LIBRARY_PATH':'bundled','LD_LIBRARY_PATH_ORIG':'system',
                'DYLD_LIBRARY_PATH':'bundled'},clear=True):
            env=support.external_process_environment()
            self.assertEqual(env['LD_LIBRARY_PATH'],'system')
            self.assertNotIn('LD_LIBRARY_PATH_ORIG',env)
            self.assertNotIn('DYLD_LIBRARY_PATH',env)
            self.assertEqual(os.environ['LD_LIBRARY_PATH'],'bundled')

    def test_desktop_open_uses_native_command_with_literal_path(self):
        path=Path('folder with spaces')
        for platform,command in [('darwin','open'),('linux','xdg-open')]:
            with self.subTest(platform=platform), patch.object(support.sys,'platform',platform), \
                    patch.object(support.subprocess,'Popen') as spawn:
                support.open_path(path)
                self.assertEqual(spawn.call_args.args[0],[command,str(path.resolve())])

    def test_frozen_browser_uses_native_node_executable(self):
        for platform,node in [('win32','node.exe'),('darwin','node'),('linux','node')]:
            with self.subTest(platform=platform), patch.object(support.sys,'platform',platform), \
                    patch.object(support.sys,'frozen',True,create=True), patch.dict(os.environ,{},clear=True):
                support.configure_bundled_runtime()
                self.assertEqual(Path(os.environ['PLAYWRIGHT_NODEJS_PATH']).name,node)
                self.assertEqual(Path(os.environ['PLAYWRIGHT_BROWSERS_PATH']),support.resource_root()/'browser-runtime')

    def test_linux_respects_xdg_config_location(self):
        location=Path('user config').resolve()
        with patch.object(support.sys,'platform','linux'), patch.dict(os.environ,{'XDG_CONFIG_HOME':str(location)}):
            self.assertEqual(support.settings_directory(),location/'sketchfab-local')


if __name__=='__main__':unittest.main()
