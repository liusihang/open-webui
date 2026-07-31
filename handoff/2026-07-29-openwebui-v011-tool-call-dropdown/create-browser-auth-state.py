from __future__ import annotations

import json

from open_webui.utils.auth import create_token


USER_ID = "b6826286-1251-4576-b3a0-e109ff085a61"
ORIGIN = "http://192.168.2.238:18085"


print(
    json.dumps(
        {
            "cookies": [],
            "origins": [
                {
                    "origin": ORIGIN,
                    "localStorage": [
                        {
                            "name": "token",
                            "value": create_token({"id": USER_ID}),
                        }
                    ],
                }
            ],
        }
    )
)
