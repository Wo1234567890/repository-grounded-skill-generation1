---
name: rgkp-residual
description: Repository-grounded guidance for the target task.
---

# Residual Skill

## Execution Guidance

### 1

Check whether Ghost's permissions system requires explicit resource registration for `audit_logs`. Search for how existing resources like 'actions' are registered:
- `grep -r '"actions"' ghost/core/core/server/data/fixtures/ -l`
- `grep -r '"actions"' ghost/core/core/shared/ -l`

If a permissions fixtures file (e.g., `fixtures.json`) or app-actions file lists resources with allowed roles, add an `audit_logs` entry with browse and read permissions for Owner and Administrator roles, following the same pattern as 'actions'. Do this before the route and controller are considered complete.

### 2

In `audit-logs.test.js`, do not reference `body` inside chained method arguments. Use the pre-defined `matchAuditLog` constant directly in `matchBodySnapshot()`.

- For the empty-collection case, follow the labels test pattern: `.matchBodySnapshot()` with no arguments.
- For tests validating actual audit log structure, use: `await agent.get('audit_logs/').expectStatus(200).matchBodySnapshot({audit_logs: [matchAuditLog]}).matchHeaderSnapshot({...})`.
- If you need `body` for further assertions, make a separate request or use a `.then()` callback.

### 3

Since no `audit_logs` fixture set exists, create test data directly in the test. In the `before` or `beforeEach` hook, after `agent.loginAsOwner()`, add:
```js
await models.AuditLog.add(
  {user_id: testUserId, action: 'test.action', context: '{}'},
  {context: {internal: true}}
);
```
Obtain `testUserId` from fixtures, e.g., `fixtureManager.get('users', 0).id` or `testUtils.DataGenerator.Content.users[0].id`. This ensures the browse test always has at least one record and can validate the full structure with `matchBodySnapshot({audit_logs: [matchAuditLog]})`.
