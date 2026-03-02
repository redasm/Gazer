---
name: weather
description: Check weather for any city worldwide using web search.
allowed-tools: web_search
---

# Weather Checking

When the user asks about weather (e.g. "What's the weather in Beijing?"), use the `web_search` tool.

## How to Execute

Search for: `<city> weather`

## Example

User: Is it raining in London?
Action: call `web_search(query="London current weather rain")`
Observation: (Search results showing light rain)
Reply: Yes, search results show London currently has light rain, about 12 degrees.
