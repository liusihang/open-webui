# Search tools

Version-controlled sources for the dynamic Open WebUI `gpt_web_search` and
`elsevier_article_search` tools. These are imported through the normal Open
WebUI Tool API; the application does not automatically install or update them
from this directory.

Configure credentials, service endpoints, proxy settings and optional embedding
or reranking services through each tool's existing admin Valves. Do not place
credentials in source control. Updating source must preserve existing Valves and
access controls.

Offline format regressions require Python 3.11+, Pydantic 2 and aiohttp:

```sh
python -m unittest discover -s tools/search/tests -p 'test_*.py'
python -m pytest backend/tests/test_search_media_contract.py
```

The tests mock upstream transport. They do not contact a search/model service,
download images, import the application/DB or change a deployed tool.
