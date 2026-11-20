# 0004: EUR 2/day demo cap, bring-your-own key beyond it

**Decision.**
- The shared demo key has a hard spend cap of EUR 2 per day. When it is reached, live runs on the shared key
  stop.
- Visitors can then watch recorded runs, or paste their own Mistral or DeepSeek key.

**Why.** At about EUR 0.02-0.05 per case, EUR 2 covers roughly 40-100 live runs a day. That is enough for
recruiters and curious visitors, and the cost stays bounded whatever happens.

**How keys are handled.**
- A visitor's key lives in server memory for their session only. It is never written to disk, logs or traces.
- It is used only for that visitor's runs and dropped at session end or after 1 hour.
- The page states which provider receives the visitor's text.

**Given up.** Unlimited free use.
