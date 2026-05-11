# Web Search

Tools for searching the public web and fetching page contents. Use sparingly — prefer the wiki and training-data knowledge when they suffice.

## Tools

- `web_search(query, num_results?)` — search the public web. **Only use for context that may need recent information** — current events, library/API changes, third-party docs that move, news. Don't reach for the web when training-data knowledge or the user's wiki would do. Returns short snippets; follow up with `open_urls` for full content. You can run this alongside the search_wiki call if needed.
- `open_urls(urls)` — fetch one or more web pages and return their full contents. Pass every URL you want to read in a single call (`urls` is an array, fetched concurrently server-side); don't issue parallel `open_urls` calls. Use after `web_search` for the most promising results, or when the user gives you URL(s) directly.
