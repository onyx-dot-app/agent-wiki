# Work Progress Tracker

## Random UX Things
- [] Mark documents that haven't been touched in a long time.
- [] Chat and triggers need to know who the user is
- [] Chat needs to know what page the user is on
- [] Make sure when user is editing manually, the doc is locked from updates
- [] Make sure the user edit is loading the latest so it doesn't write over a save
- [] Triggers need to know about the document

## User Auth
- [x] Basic
- [x] OAuth
- [x] Directory and Page level Ownership
- [x] Propagation of ownership changes with moving docs around
- [x] Access to triggers (creation, firing as document/dir permissions change)

## LLM Interface
- [x] Basic interface + tools
- [] Support for major LLM providers
- [] Nice UX for LLM setup

## Chat Harness
- [x] Basic chat loop
- [x] Chat history
- [x] Tools:
  - [x] search_wiki
  - [x] read_page
  - [x] web_search
  - [x] open_urls
  - [x] get_trigger_destinations
  - [x] create_trigger
  - [x] update_trigger
  - [x] edit_doc
  - [x] multi_edit
  - [x] write_doc
  - [x] create_directory
  - [x] move_path
  - [] explain_functionality (needs manual rewrite)
  - [x] run_bash

## Document Updater
- [] From Onyx connector pushes
- [] From natural language
- [] From direct diff from Agents
- [x] Agent activities are tracked as metadata to the docs

## Background Tasks
- [x] Implement queue
- [x] Implement workers
- [] Document processing queue
- [x] Trigger evaluation queue
- [x] BM25 indexing queue
- [] Cron jobs and task queuing
- [x] Task creation (trigger evals + BM25 update) on document updates from agents, chat, human saving

## Core Architecture
- [x] Flask API
- [x] SqlAlchemy ORM
- [x] Postgres DB
- [x] pg_textsearch for BM25
- [x] pgmq for task queue
- [x] Alembic for migrations
- [x] Git-backed wiki storage (custom subprocess wrapper)
- [x] Next.js 14 frontend (App Router)
- [x] TypeScript
- [x] Nginx reverse proxy
- [x] Provider-agnostic LLM client seam

## Infra/Deployment
- [x] Docker Compose
- [x] Helm
- [] Cloud deployment handling

## CI/Testing
- [x] Unit test framework
- [x] Integration tests
- [x] Reasonable coverage for existing features
- [x] Positive and negative tests for ACLs and Authz
