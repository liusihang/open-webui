"""Run real queue/publication SQL in a process isolated from other test imports."""

from pathlib import Path
import subprocess
import sys
import unittest


class IngestionSQLRegression(unittest.TestCase):
    def test_production_sql_contract_in_subprocess(self):
        source = Path(__file__).resolve().parents[1] / 'ingestion/sql_cases.py'
        result = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
