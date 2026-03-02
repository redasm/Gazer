---
name: web
description: Search the web and fetch page content for information retrieval.
allowed-tools: web_search web_fetch browser
---

# Web Information Retrieval

You can search the web and read web pages.

## Tools

- `web_search` -- Search the web. Returns titles, URLs, and snippets. Best for factual queries, news, documentation lookups.
- `web_fetch` -- Fetch a specific URL and extract readable text. Use when you have a URL and need the content.
- `browser` -- Full browser automation for interactive pages. Use only when web_fetch is insufficient (JS-heavy sites, login required).

## Strategy

1. Start with `web_search` for discovery.
2. Use `web_fetch` to read specific pages from search results.
3. Fall back to `browser` only for interactive tasks or JS-rendered content.
