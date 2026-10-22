# Cache Test: 12 Practice Documents, With and Without Cache

Every model call is listed in `output/cache_test.json` (48 calls in total). Each document took 2 model calls: the first reads the document with the tool, and the second returns the answer. The agent's instructions were the same in both setups. All calls were answered by `gpt-6-luna-global`, and no trap tool was called.

- **With cache:** p01 ran alone first to warm the cache, then the other 11 ran 6 at a time. Every call after the first one got about 10,540 cached tokens.
- **Without cache:** I ran `agent.py --no-cache`, which puts a random line at the very start of every model call. All 12 ran 6 at a time. All 24 calls showed **0 cached tokens**.

## Cost

| Setup | Calls | Input tokens (cached / cache write / plain) | Output tokens | Total cost | Cost per document |
|---|---|---|---|---|---|
| With cache | 24 | 242,709 / 12,736 / 3,024 | 1,781 | **$0.00521** | **$0.00043** |
| Without cache | 24 | 0 / 256,017 / 3,024 | 1,820 | **$0.03321** | **$0.00277** |

**Caching saved 84.3%** ($0.00521 vs $0.03321). With the warm-up run left out, a typical cached document costs $0.00033, which is 88% less.

## Compared with the Problem 2 estimate

Problem 2 estimated 10,058 handbook tokens, and $0.0010 per call without caching or about $0.0001 per call with it.

- **The handbook goes out on every model call, not once per document.** Each document needs 2 calls, so it pays for the handbook twice: about 20,100 handbook tokens per document. That alone doubles the estimate per document.
- **The real prompt is a bit bigger.** prompt.md adds about 280 tokens (10,342 by tiktoken). The real API counted about 10,690 input tokens per call, because the tool definitions, the output format, and the document text all add to it. The cached prefix was about 10,540 tokens, so the tiktoken estimate was within about 2%.
- **Cached calls match the estimate.** The handbook costs about $0.0001 per cached call, as predicted. A cached document costs $0.00033 because it has 2 calls, plus some uncached document text and output.
- **Uncached calls cost more than estimated.** The estimate assumed $0.10 per million input tokens. But this provider reports almost every uncached input token as a **cache write**, which `prices.json` prices at $0.125, or 25% more. So the no-cache document cost of $0.00277 is about 2 calls × 10,800 tokens × $0.125 per million, plus output.

## Projection: 1,000 documents a day for 30 days (30,000 documents)

| Setup | Per document | 30 days |
|---|---|---|
| With cache (steady state, warm cache) | $0.00033 | **about $10** |
| With cache (this test's average, including the warm-up) | $0.00043 | about $13 |
| Without cache | $0.00277 | **about $83** |

Caching saves about $70 to $73 a month at this volume. This assumes the cache stays warm. At 1,000 documents a day the gaps between calls should be short, but overnight gaps would mean one extra warm-up call each morning, which costs almost nothing. It also assumes each document still takes 2 calls.

## What surprised me

1. **"Without cache" still paid cache-write prices.** I expected the no-cache calls to be charged the plain $0.10 input price. Instead, the API reported about 10,600 cache-write tokens on every call. The provider writes every new prefix to the cache automatically, and this one is never reused because of the random line. So no caching costs *more* than the Problem 2 estimate assumed. At the plain input price it would have been $0.00223 per document, and caching would still save about 80%.
2. **Cached calls also report small cache writes.** Even cached calls reported about 20 to 280 cache-write tokens, for the new text after the cached prefix (the document and the tool result). These cost very little.
3. **Running 6 at once didn't hurt cache hits.** After the warm-up, all 11 documents running in parallel got the same 10,542 cached tokens on their first call. I expected some misses from requests landing on different servers.
4. **One document got a different answer in each setup.** For p12 (Blue Hen PDF), the with-cache run reported the lead time as unknown and decided `do_not_recommend`. The no-cache run read it as 13 days and decided `recommend`. The other 11 documents got identical answers in both setups. Caching doesn't change what the model reads, so this is ordinary run-to-run randomness, but it means p12's answer isn't reliable yet.
5. **p09 cost more than the others.** Its second call produced 376 to 423 output tokens, where other documents produced about 80 to 110, so it cost about 50% more in the with-cache setup. No trap tool was called.
