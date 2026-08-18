<!-- loom protocol v1 — written by `loom init`; edits here are overwritten on re-init -->
## loom — shared-repo coordination protocol

Before any code change in this repo:
1. Write a spec from loom's `templates/spec.md` (one page, all five sections, no unfilled brackets).
2. Resolve every write target and every assume to canonical node IDs with the loom `resolve_nodes`
   tool. IDs look like `relative/path.py::Class/method`; whole files are `relative/path.ext`.
3. Call `declare_plan(...)`. If the response carries conflicts, read each embedded spec, replan to
   build against its DECLARED interfaces — never against in-flight code — adjust your targets, and
   declare again. Warnings mean someone reads what you write, or you read what they write: honor
   their spec.
4. Edit normally. If the loom gate blocks an edit, follow the message: it either hands you the
   owning plan's spec to build around, or tells you to rescope, or to declare a plan first.
5. If your work grows beyond the declared targets, call `rescope(plan_id, add_targets, add_assumes)`
   BEFORE touching the new ground.
6. When tests pass and the branch merges, call `release(plan_id, agent)`.

Claims expire on a TTL (30 min) and renew automatically while you edit. If `renew` or `check` says
your plan is gone, re-declare — do not edit around a deny.
