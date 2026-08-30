"""Prospects outreach backend: website discovery, contact collection, and a
mailto-only outreach pipeline (draft -> review -> approve -> mailto).

No SMTP, no automated sending: the only artefact produced for a human to act on
is an encoded ``mailto:`` URL. See ``mailto.py`` / ``mailto_txn.py``.
"""
