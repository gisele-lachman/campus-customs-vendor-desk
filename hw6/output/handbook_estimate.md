# Handbook Token and Cost Estimate

## Token count

**About 10,058 tokens.** This is an estimate.

- File: `data/shop_handbook.md` (29,241 bytes)
- Tool: `tiktoken` 0.14.0 (OpenAI's open-source tokenizer), encoding `o200k_base`. This is the closest standard encoding available for recent OpenAI models. OpenAI doesn't publish a tokenizer for gpt-6-luna, so the real count may differ slightly.
- For comparison, the older `cl100k_base` encoding gives 10,184 tokens.
- This counts only the handbook text. It leaves out system prompts, message formatting overhead, the vendor document, and the model's output.

## Prices (from `data/prices.json`, gpt-6-luna, per 1M tokens)

| Type | Price |
|---|---|
| Input | $0.10 |
| Cached input | $0.01 |
| Cache write | $0.125 |

## Cost of sending the handbook

**Without caching** (every call pays the input price):

- 1 call: 10,058 × $0.10 / 1,000,000 = **$0.0010**
- 1,000 calls: 1,000 × 10,058 × $0.10 / 1,000,000 = **$1.01**

**With caching**

Assumption: the first call writes the handbook to the cache at the cache-write price ($0.125/1M). The other 999 calls read it at the cached-input price ($0.01/1M). We also assume the cache stays warm for every call and the handbook sits at the start of the prompt, unchanged each time.

- 1 call: 10,058 × $0.125 / 1,000,000 = **$0.0013** (slightly more than without caching, because a single call only pays the write premium)
- 1,000 calls:
  - First call: 10,058 × $0.125 / 1,000,000 = $0.00126
  - Remaining 999 calls: 999 × 10,058 × $0.01 / 1,000,000 = $0.10048
  - Total: **$0.10**

**Result:** at 1,000 calls, caching cuts the handbook cost from about $1.01 to about $0.10, roughly 90% less.

If the first call is charged the normal input price instead of the cache-write price, the 1,000-call total is $0.00101 + $0.10048 = $0.1015, so still about $0.10.
