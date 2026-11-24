---
name: rgkp-residual
description: Repository-grounded guidance for the target task.
---

# Residual Skill

## Validated Knowledge

### 1

The repository maps source paths to namespaces via directory structure under `src/` and `test/`. The alert API source is `src/metabase/pulse/api/alert.clj` → namespace `metabase.pulse.api.alert`; its test is `test/metabase/pulse/api/alert_test.clj` → namespace `metabase.pulse.api.alert-test`. A new Malli validation test file must follow this convention: `test/metabase/pulse/api/alert_malli_test.clj` with namespace `metabase.pulse.api.alert-malli-test`, not `test/metabase/api/alert_malli_test.clj` as listed in the task requirements.

## Execution Guidance

### 1

In the test file, every `mt/user-http-request` call that verifies a status code must be wrapped in `(is ...)`. Use:
```clojure
(is (mt/user-http-request :crowberto :post 400 "alert" {...}))
```
or to also assert on the response value:
```clojure
(is (= expected-value (mt/user-http-request :crowberto :post 400 "alert" {...})))
```
Apply this to all of the following tests, which currently lack an `is` wrapper: `post-alert-invalid-card-id-test`, `post-alert-missing-card-id-test`, `post-alert-empty-channels-test`, `post-alert-missing-channels-test`, `post-alert-invalid-channel-type-test`, `post-alert-invalid-alert-condition-test`, `post-alert-missing-alert-condition-test`, `put-alert-empty-channels-test`, `put-alert-invalid-condition-test`, and `post-alert-nonexistent-card-test`.

### 2

In `src/metabase/pulse/api/alert.clj`, before calling `notification.api/create-notification!` in the POST endpoint, transform the alert request body into the notification shape. The `create-notification!` function expects a map with `:payload_type` (keyword), `:creator_id`, `:payload` (containing `:card_id` and the send condition derived from `alert_condition`), `:subscriptions`, and `:handlers` (derived from `channels`). Consult `notification->pulse` (the reverse transform, already defined in `alert.clj`) to understand the field mapping, and see `test/metabase/notification/api/notification_test.clj` lines 564–568 for an example of the expected shape.

After schema validation passes and the notification map is built, delegate as:
```clojure
(-> (notification.api/create-notification! <notification-map>)
    notification->pulse)
```
`notification.api/create-notification!` is already required in this namespace. Similarly, the PUT endpoint should call the appropriate update function rather than returning a stub. See how `notification.api/list-notifications` results are piped through `notification->pulse` in the existing GET endpoint (lines 96–101) for the established pattern.
