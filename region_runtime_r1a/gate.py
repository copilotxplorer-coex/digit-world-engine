"""R1-A exact-count gate. Use this full gate after R1-A, not R0's pinned 455 gate."""
import argparse
from pathlib import Path
import shutil
import sys
import unittest

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--full",action="store_true");a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    if a.full:
        if not shutil.which("node"):print("Node.js required in PATH",file=sys.stderr);return 2
        suite=unittest.defaultTestLoader.discover(str(root/"tests"),pattern="test_*.py",top_level_dir=str(root));expected=510
    else:suite=unittest.defaultTestLoader.loadTestsFromName("tests.test_region_runtime_r1a");expected=55
    if suite.countTestCases()!=expected:print("Unexpected discovery count",suite.countTestCases(),"expected",expected,file=sys.stderr);return 2
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped or result.expectedFailures or result.testsRun!=expected:return 1
    print("PASS: Region Runtime R1-A gate; tests="+str(expected)+"; skipped=0");return 0
if __name__=="__main__":raise SystemExit(main())
