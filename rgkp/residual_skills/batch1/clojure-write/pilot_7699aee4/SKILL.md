---
name: rgkp-residual
description: Repository-grounded guidance for the target task.
---

# Residual Skill

## Execution Guidance

### 1

When implementing `format-currency-value` in `currency_formatter.clj`, require `metabase.util.currency` and use `currency/supports-symbol?` to decide the display style: if the currency code (e.g., "USD", "CNY") is supported, use `(get-in currency/currency [(keyword currency-code) :symbol])` to get the symbol (e.g., "$", "CN¥"); otherwise use the currency code string directly. Concatenate the identifier with the numeric value as a string, returning a formatted result like "$100.50" or "CN¥100.50". This matches the pattern used in `streaming/common.clj`'s `currency-identifier` function. Apply `format-currency-value` inside `currency-formatter-xform` where values are processed per row.
