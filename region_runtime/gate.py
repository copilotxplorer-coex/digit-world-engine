"""Strict regression gate with exact discovery count and zero silent skips."""
import argparse
from pathlib import Path
import shutil
import sys
import unittest

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full",action="store_true")
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    if args.full:
        if not shutil.which("node"):
            print("Full gate requires Node.js in PATH",file=sys.stderr);return 2
        suite=unittest.defaultTestLoader.discover(str(root/"tests"),pattern="test_*.py",top_level_dir=str(root))
        expected=455
    else:
        suite=unittest.defaultTestLoader.loadTestsFromName("tests.test_region_runtime_r0")
        expected=44
    count=suite.countTestCases()
    if count!=expected:
        print("Unexpected test count: "+str(count)+" expected "+str(expected),file=sys.stderr);return 2
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped or result.expectedFailures or result.testsRun!=expected:return 1
    print("PASS: Region Runtime R0 gate; tests="+str(count)+"; skipped=0")
    return 0
if __name__=="__main__":raise SystemExit(main())
