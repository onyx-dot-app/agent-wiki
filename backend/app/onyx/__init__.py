"""Outbound Onyx integration (Craft launches + Connect-Onyx account link).

The inbound direction (Onyx pushing documents into the wiki) lives in
``app/ingest/``; this package is the reverse: calling Onyx's build API as
a specific user via their stored PAT. See the admin "Onyx Connection"
page for instance config and "Engineering Projects/Craft Integration" on
the wiki for the design.
"""
