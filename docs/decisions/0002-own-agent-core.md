# 0002: Small, framework-free agent core

**Decision.** Write a small agent core (agent loop, tools, tool manager, provider interface, agents as tools)
instead of adopting an agent framework.

**Why.**
- Policy guards must run before every tool call, and tracing must cover every model and tool call. Owning the
  loop makes both first-class instead of bolted on.
- The loop is bounded: caps on steps, tokens, time and cost. Every tool error goes back to the model as a result
  and never crashes a run.
- A few hundred lines that can be read in full are easier to reason about, test and replay than a framework.

**Given up.** Framework features such as built-in memory stores and graph editors. They are not needed here.
