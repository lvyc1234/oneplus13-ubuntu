"""Check the bounded owner-touch observer accepts 120 seconds."""
import subprocess,sys
from pathlib import Path
import unittest

class ManualTouch(unittest.TestCase):
    def test_120_seconds_is_declared(self):
        script=Path(__file__).resolve().parents[1]/'tools/oneplus13/manual-touch-observer.py'
        result=subprocess.run([sys.executable,str(script),'--help'],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('120',result.stdout)

if __name__=='__main__':unittest.main()
