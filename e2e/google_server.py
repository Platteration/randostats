"""The walk's server with sign-in with Google set up, for e2e/test_site.py.

`randostats serve`'s app on loopback, its Google half answered by the fake in
tests/fake_google.py: the token endpoint is handed to create_app(fetch=...), as the
unit tests hand it, and the consent screen is the walk's own (it answers the
browser's request for Google's authorize address at the protocol). Nothing points
the app at another host: google.py's addresses are constants.

    python e2e/google_server.py DB PORT [PASSWORD]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

import fake_google  # noqa: E402
import uvicorn  # noqa: E402

from randostats.api import create_app  # noqa: E402


def main(argv: list[str]) -> None:
    db, port = argv[0], int(argv[1])
    password = argv[2] if len(argv) > 2 and argv[2] else None
    config = fake_google.config(f"http://127.0.0.1:{port}/", subs=(fake_google.OWNER["sub"],))
    app = create_app(db, use_llm=False, password=password, google_config=config, fetch=fake_google.FakeGoogle())
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main(sys.argv[1:])
