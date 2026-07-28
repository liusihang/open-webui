#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    token_path = Path(sys.argv[1])
    origin = sys.argv[2]
    output_path = Path(sys.argv[3])
    state = {
        'cookies': [],
        'origins': [
            {
                'origin': origin,
                'localStorage': [{'name': 'token', 'value': token_path.read_text().strip()}],
            }
        ],
    }
    output_path.write_text(json.dumps(state, separators=(',', ':')))
    os.chmod(output_path, 0o600)


if __name__ == '__main__':
    main()
