"""WSGI entry point for production (waitress on munchlax).

The Flask dev server (`labeler-web` / `app.main()`) is for local development only.
In the munchlax deployment, waitress calls this factory:

    waitress-serve --call labeler.wsgi:app

`--call` means "call this to get the app", so `app()` returns a fresh WSGI
application from the same factory the dev entry point uses. Keeping both paths on
`create_app()` means production and dev serve identical code.

See specs/munchlax-deployment.md.
"""

from labeler.web.app import create_app


def app():
    return create_app()
