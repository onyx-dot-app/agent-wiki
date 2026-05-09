"""Chat persistence — sessions and messages backing the in-app ChatUI.

Sessions are per-user; the source of truth lives in Postgres
(``chat_sessions`` / ``chat_messages``). The HTTP layer in
``app/api/chat.py`` and the title-generation task in
``app/tasks/chat_title.py`` both go through ``app.chat.sessions`` —
don't touch the ORM models directly from the API or from tasks.
"""
