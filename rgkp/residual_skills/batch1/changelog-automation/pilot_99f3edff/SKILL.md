---
name: rgkp-residual
description: Repository-grounded guidance for the target task.
---

# Residual Skill

## Execution Guidance

### 1

In `examples/advanced_config/.github_changelog_generator`, add `include-labels=enhancement,bug,breaking-change,security` in the label configuration section alongside `exclude-labels`. The config file key `include-labels` maps to the CLI option `--include-labels x,y,z` / `options[:include_labels]` (see `lib/github_changelog_generator/argv_parser.rb` line 118), restricting changelog output to only issues/PRs with the specified labels.
