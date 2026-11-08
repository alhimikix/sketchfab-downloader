from pathlib import Path
import subprocess
import unittest

from playwright._impl._driver import compute_driver_executable
from platform_support import external_process_environment


class SketchfabLoaderTests(unittest.TestCase):
    def test_module_observer_supports_protected_and_legacy_geometry(self):
        node,_=compute_driver_executable()
        result=subprocess.run([str(node),str(Path(__file__).with_suffix('.js'))],
            capture_output=True,text=True,timeout=30,env=external_process_environment())
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('SKETCHFAB_LOADER_TEST_OK',result.stdout)


if __name__=='__main__':unittest.main()
