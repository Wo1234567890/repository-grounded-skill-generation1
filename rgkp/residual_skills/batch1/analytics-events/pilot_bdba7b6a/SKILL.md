---
name: rgkp-residual
description: Repository-grounded guidance for the target task.
---

# Residual Skill

## Execution Guidance

### 1

Name the test file `frontend/test/metabase/lib/analytics.unit.spec.ts` (not `analytics.test.ts`). The Jest `testMatch` in `jest.config.js` only discovers `*.unit.spec.{js,jsx,ts,tsx}` files, so `analytics.test.ts` will never be run by `bun run test-unit`.
