"""Offline eval suite for agents that update the wiki.

Lives outside ``tests/`` because runs are slow (real LLM calls), need a
provider/model selected at the command line, and are not part of CI.

See ``backend/evals/README.md`` for the full surface map and how to run.
"""
