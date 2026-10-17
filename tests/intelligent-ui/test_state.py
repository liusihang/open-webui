"""Small dependency-free regression tests for the persisted interaction envelope."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[2] / 'backend/open_webui/utils/intelligent_ui.py'
spec = importlib.util.spec_from_file_location('intelligent_ui', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class StateTest(unittest.TestCase):
    def validate(self, key, value):
        return module.validate_intelligent_ui_state({'version': 1, 'blocks': {'block': {key: value}}})

    def test_multiline_drafts_and_controls(self):
        self.validate('writing:doc', 'Draft\n\t' + 'x' * 16000)
        self.validate('shared:notes', 'Line one\nLine two')
        for key, value in [('writing:doc', 'x' * 16385), ('shared:notes', 'x' * 257), ('writing:doc', '\x00'), ('tab:root', '\n'), ('shared:number', 1e13)]:
            with self.assertRaises(ValueError):
                self.validate(key, value)

    def test_whole_envelope_remains_bounded(self):
        with self.assertRaises(ValueError):
            module.validate_intelligent_ui_state({'version': 1, 'blocks': {'block': {f'writing:{n}': 'x' * 16384 for n in range(9)}}})

    def test_new_catalog_preserves_existing_contracts(self):
        for component in ['WritingBlock', 'CodeBlock', 'AppBlock', 'Conditional', 'ImageGallery', 'DataTable', 'MetricCard', 'heatmap']:
            self.assertIn(component, module.INTELLIGENT_UI_PROMPT)
        self.assertIn('Video and map components are not part', module.INTELLIGENT_UI_PROMPT)

if __name__ == '__main__':
    unittest.main()
