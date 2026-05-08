-- Extend llm_settings with credentials for the additional providers.
-- gemini_api_key: Google Gemini API key.
-- ollama_base_url: e.g. "http://localhost:11434". Empty string means use the
-- provider's default; Ollama doesn't take an API key in v0.
ALTER TABLE llm_settings ADD COLUMN gemini_api_key   TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_settings ADD COLUMN ollama_base_url  TEXT NOT NULL DEFAULT '';
