"""Flask web app for the VC-500W label designer.

Sits on top of the verified core (protocol/render/compose/status/config). See
specs/flask-app.md. Runtime data lives under ~/.labeler/ (see runtime.py), kept
separate from the code repo per the workspace code/runtime split rule.
"""
