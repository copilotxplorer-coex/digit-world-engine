"""Exact-count R1-B gate; replaces older pinned full-gate entrypoints after install."""
import argparse,shutil,sys,unittest
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--full',action='store_true');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    if args.full:
        if not shutil.which('node'):print('Node.js required in PATH',file=sys.stderr);return 2
        suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='test_*.py',top_level_dir=str(root));expected=922
    else:suite=unittest.defaultTestLoader.loadTestsFromNames(['tests.test_region_runtime_r1b','tests.test_region_runtime_r1b_fencing','tests.test_region_runtime_r1b_frontiers','tests.test_region_runtime_checkpoint_v01','tests.test_region_runtime_checkpoint_persistence_lab','tests.test_region_runtime_checkpoint_trust_lab']);expected=412
    if suite.countTestCases()!=expected:print('Unexpected discovery count',suite.countTestCases(),'expected',expected,file=sys.stderr);return 2
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped or result.expectedFailures or result.testsRun!=expected:return 1
    print('PASS: Region Runtime Trust Binding Lab v0.1 gate; tests='+str(expected)+'; skipped=0');return 0
if __name__=='__main__':raise SystemExit(main())
