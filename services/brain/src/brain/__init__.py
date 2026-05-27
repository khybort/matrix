"""Matrix Brain — the Opus-4.7 "ask anything" agent.

A read-only conversational agent with a deep tool belt over the whole system
(AGE graph + relational state + market data). Exposes an SSE /chat endpoint
consumed by the web dashboard, Telegram, and any API client. Never mutates
trading state: no write tools, a can_use_tool deny-hook, and SQL/Cypher
read-only guards.
"""
