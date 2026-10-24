"""Exercise production admission and disk spool, without application/DB startup."""

import io
import tempfile
import unittest
from pathlib import Path
import sys
import importlib.util
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / 'backend/open_webui/retrieval/admission.py'
spec = importlib.util.spec_from_file_location('ingestion_admission', source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
AdmissionError, AdmissionLimits, ChunkSpool, inspect_source = (
    module.AdmissionError,
    module.AdmissionLimits,
    module.ChunkSpool,
    module.inspect_source,
)


class AdmissionTests(unittest.TestCase):
    def test_killed_job_scratch_cleanup_preserves_other_jobs_and_links(self):
        worker_source = source.with_name('ingestion_worker.py')
        worker_spec = importlib.util.spec_from_file_location('isolated_worker', worker_source)
        worker = importlib.util.module_from_spec(worker_spec)
        worker_spec.loader.exec_module(worker)
        identity = 'a1a83787-d748-43bc-8b01-d87dbd1bfcd4'
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            owned = Path(root) / ('job-' + identity + '-scratch')
            owned.mkdir()
            (owned / 'chunks.jsonl').write_text('incomplete candidate')
            other = Path(root) / 'job-other-scratch'
            other.mkdir()
            link = Path(root) / ('job-' + identity + '-link')
            link.symlink_to(outside, target_is_directory=True)
            with patch.dict('os.environ', {'INDEXING_TEMP_DIR': root}):
                worker.cleanup_job_temp(identity)
                with self.assertRaises(ValueError):
                    worker.cleanup_job_temp('../')
            self.assertFalse(owned.exists())
            self.assertTrue(other.is_dir())
            self.assertTrue(link.is_symlink())
            self.assertTrue(Path(outside).is_dir())

    def test_large_declared_numeric_matrix_is_not_document_text(self):
        sample = ('Matrix Size (270,270,300)\nnbVal=21870000\n' + ('0.0 ' * 270 + '\n') * 60).encode()
        with self.assertRaisesRegex(AdmissionError, 'numeric'):
            inspect_source(io.BytesIO(sample), size=67_833_973)

    def test_paper_with_equations_and_numbers_is_allowed(self):
        sample = ('Results of the model are given in Table 1. x=42; p=0.002.\n' * 100).encode()
        inspect_source(io.BytesIO(sample), size=67_833_973)

    def test_small_numeric_table_is_allowed(self):
        inspect_source(io.BytesIO(b'1 2 3\n' * 100), size=600)

    def test_code_is_not_rejected_for_dimensions_in_comments(self):
        text = b'# matrix shape: (270, 270, 300)\nfor i in range(42):\n    print(i)\n' * 100
        inspect_source(io.BytesIO(text), size=30_000_000)

    def test_preflight_restores_stream_position(self):
        stream = io.BytesIO(b'Research text.\n' * 500)
        stream.seek(7)
        inspect_source(stream, size=8000)
        self.assertEqual(stream.tell(), 7)

    def test_spool_stops_generator_at_budget_not_after_full_split(self):
        consumed = []

        def chunks():
            for i in range(100_000):
                consumed.append(i)
                yield {'text': 'hello', 'metadata': {}}

        with tempfile.TemporaryDirectory() as root:
            with ChunkSpool(Path(root), AdmissionLimits(max_chunks=3)) as spool:
                with self.assertRaises(AdmissionError):
                    spool.write(chunks())
        self.assertEqual(len(consumed), 4)

    def test_spool_checks_encoded_bytes_and_yields_bounded_batches(self):
        with tempfile.TemporaryDirectory() as root:
            with ChunkSpool(Path(root), AdmissionLimits(max_text_bytes=100, max_chunks=20)) as spool:
                spool.write({'text': '中文', 'metadata': {'n': i}} for i in range(10))
                self.assertEqual(spool.text_bytes, 60)
                self.assertEqual([len(batch) for batch in spool.batches(3)], [3, 3, 3, 1])
                self.assertTrue(spool.digest)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_spool_rejects_text_and_temporary_storage_overflow(self):
        for limits in (AdmissionLimits(max_text_bytes=10), AdmissionLimits(max_spool_bytes=10)):
            with tempfile.TemporaryDirectory() as root:
                with ChunkSpool(Path(root), limits) as spool:
                    with self.assertRaises(AdmissionError):
                        spool.write([{'text': 'x' * 20, 'metadata': {}}])

    def test_no_empty_index_can_be_published(self):
        with tempfile.TemporaryDirectory() as root:
            with ChunkSpool(Path(root), AdmissionLimits()) as spool:
                with self.assertRaisesRegex(AdmissionError, 'empty'):
                    spool.write([])


if __name__ == '__main__':
    unittest.main()
