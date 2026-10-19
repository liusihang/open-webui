"""Test the real identity block without importing application configuration."""

from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = (Path(__file__).resolve().parents[2] / 'backend/open_webui/env.py').read_text()
START = SOURCE.index("WEBUI_NAME = os.getenv('WEBUI_NAME'")
END = SOURCE.index('# LICENSE covers this Open WebUI branding surface, including this favicon', START)


def configured_name(environment):
    namespace = {'os': SimpleNamespace(getenv=environment.get)}
    exec(compile(SOURCE[START:END], 'webui_identity', 'exec'), namespace)
    return namespace['WEBUI_NAME']


class WebUINameTests(unittest.TestCase):
    def test_default_name_is_unchanged(self):
        self.assertEqual(configured_name({}), 'Open WebUI')

    def test_configured_name_is_used_verbatim(self):
        self.assertEqual(configured_name({'WEBUI_NAME': 'AIshuofang'}), 'AIshuofang')


if __name__ == '__main__':
    unittest.main()
