# GroundedBI evaluation report

- Commit: `12b5d38`
- Seed: 42 (data 2026-01, 6 months)
- Provider: rule_based
- Run at: 2026-10-26T22:54:00+00:00

Rates are over the questions where each check applies (shown as passed/total). Arguments are only checked when the tool list is correct; grounding only for answered questions. A confident wrong answer is an answered, grounded response to a question that still failed.

## Split: dev (state: dev; gated by --fail-under)

| Group | n | Pass | Status | Tools | Arguments | Grounding | Confident wrong | p50 ms | p95 ms | max ms | Avg tools |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **overall** | 41 | 100% (41/41) | 100% (41/41) | 100% (41/41) | 100% (23/23) | 100% (23/23) | 0 | 6.5 | 13.6 | 15.9 | 1.41 |
| breakdown_category | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 6.7 | 6.7 | 6.7 | 2.0 |
| breakdown_product | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 8.4 | 8.4 | 8.4 | 2.0 |
| breakdown_region | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 6.5 | 6.5 | 6.5 | 2.0 |
| change_drivers | 5 | 100% (5/5) | 100% (5/5) | 100% (5/5) | 100% (5/5) | 100% (5/5) | 0 | 13.6 | 15.9 | 15.9 | 2.0 |
| clarify_no_intent | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | n/a | n/a | 0 | 4.0 | 4.3 | 4.3 | 1.0 |
| clarify_relative_time | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 4.0 | 4.0 | 4.0 | 1.0 |
| clarify_unknown_name | 4 | 100% (4/4) | 100% (4/4) | 100% (4/4) | n/a | n/a | 0 | 4.1 | 4.2 | 4.2 | 1.0 |
| clarify_unsupported_ranking | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 4.2 | 4.2 | 4.2 | 1.0 |
| clarify_vague | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | n/a | n/a | 0 | 4.1 | 4.2 | 4.2 | 1.0 |
| comparison | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 6.3 | 6.3 | 6.3 | 2.0 |
| false_premise | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 9.2 | 9.2 | 9.2 | 2.0 |
| monthly | 4 | 100% (4/4) | 100% (4/4) | 100% (4/4) | 100% (4/4) | 100% (4/4) | 0 | 6.6 | 8.3 | 8.3 | 2.0 |
| named_comparison | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 100% (2/2) | 100% (2/2) | 0 | 8.4 | 8.6 | 8.6 | 2.0 |
| out_of_scope_creative | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 1.6 | 1.6 | 1.6 | 0.0 |
| out_of_scope_forecast | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 1.5 | 1.5 | 1.5 | 0.0 |
| out_of_scope_injection | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 1.6 | 1.6 | 1.6 | 0.0 |
| out_of_scope_metric | 4 | 100% (4/4) | 100% (4/4) | 100% (4/4) | n/a | n/a | 0 | 1.6 | 1.6 | 1.6 | 0.0 |
| out_of_scope_unrelated | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 3.9 | 3.9 | 3.9 | 1.0 |
| partial_coverage | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 7.2 | 7.2 | 7.2 | 2.0 |
| ranking_dimension | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 8.2 | 8.2 | 8.2 | 2.0 |
| top_n | 5 | 100% (5/5) | 100% (5/5) | 100% (5/5) | 100% (5/5) | 100% (5/5) | 0 | 8.2 | 10.6 | 10.6 | 2.2 |

## Split: heldout (state: seen, inspected while diagnosing the baseline at commit 46aa0c4; measured only, never gated)

| Group | n | Pass | Status | Tools | Arguments | Grounding | Confident wrong | p50 ms | p95 ms | max ms | Avg tools |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **overall** | 10 | 100% (10/10) | 100% (10/10) | 100% (10/10) | 100% (6/6) | 100% (6/6) | 0 | 6.5 | 11.6 | 11.6 | 1.4 |
| false_premise | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 9.8 | 9.8 | 9.8 | 2.0 |
| no_data | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 5.6 | 5.6 | 5.6 | 2.0 |
| normal | 3 | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 0 | 8.4 | 10.0 | 10.0 | 2.0 |
| out_of_scope | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | n/a | n/a | 0 | 1.5 | 1.6 | 1.6 | 0.0 |
| typos | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 9.7 | 9.7 | 9.7 | 2.0 |
| vague | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 11.6 | 11.6 | 11.6 | 1.0 |
| vague_tricky | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 4.1 | 4.1 | 4.1 | 1.0 |

## Split: heldout_v2 (state: fresh; measured only, never gated)

| Group | n | Pass | Status | Tools | Arguments | Grounding | Confident wrong | p50 ms | p95 ms | max ms | Avg tools |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **overall** | 10 | 70% (7/10) | 80% (8/10) | 90% (9/10) | 100% (5/5) | 100% (5/5) | 1 | 4.7 | 8.6 | 8.6 | 1.4 |
| comparison | 1 | 0% (0/1) | 0% (0/1) | 0% (0/1) | n/a | n/a | 0 | 4.2 | 4.2 | 4.2 | 1.0 |
| named_change | 1 | 0% (0/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 1 | 8.6 | 8.6 | 8.6 | 2.0 |
| normal | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 100% (2/2) | 100% (2/2) | 0 | 6.5 | 8.2 | 8.2 | 2.0 |
| out_of_scope | 2 | 50% (1/2) | 50% (1/2) | 100% (2/2) | n/a | n/a | 0 | 1.7 | 4.7 | 4.7 | 0.5 |
| partial_coverage | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 7.2 | 7.2 | 7.2 | 2.0 |
| relative_time | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 4.3 | 4.3 | 4.3 | 1.0 |
| sloppy_wording | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 7.0 | 7.0 | 7.0 | 2.0 |
| unknown_name | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 4.2 | 4.2 | 4.2 | 1.0 |

## Failures (3)

### heldout-v2-02 (heldout_v2, named_change) **(confident wrong)**

- Question: How did Phone Max sales change from April to May 2026?
- Failed checks: must_contain
- Status: expected `answered`, actual `answered`
- Tools: expected `["get_change_drivers"]`, actual `["get_change_drivers"]`
- Missing from text: `["$302,225", "$306,621"]`
- Returned text: Revenue rose by $28,501 (1.6%), from $1,823,688 in April 2026 to $1,852,189 in May 2026. The largest driver was Tablet 11, which rose by $7,787 from $141,963 to $149,750, 27.3% of the total change. No other product accounts for more than 24.1% (next largest: Smartwatch, $6,877). This shows all products with Phone Max highlighted; it is not filtered to Phone Max. Phone Max: $4,396 (15.4% of the total change).

### heldout-v2-04 (heldout_v2, comparison)

- Question: Compare revenue in the first quarter with the second quarter of 2026
- Failed checks: status, tools, must_contain
- Status: expected `answered`, actual `clarification_needed`
- Tools: expected `["compare_periods"]`, actual `[]`
- Missing from text: `["$5,117,801", "$5,562,355"]`
- Returned text: I couldn't find "first" in the data. The regions are East, North, South and West; the products are Noise-Cancelling Headphones, Phone Lite, Phone Max, Smartwatch, StudioBook 16, Tablet 11, UltraBook 14 and Wireless Earbuds; the categories are Audio, Laptops, Phones and Wearables & Tablets. Which one did you mean? For example: "Revenue by region in June 2026" or "Why did revenue change in June 2026?".

### heldout-v2-09 (heldout_v2, out_of_scope)

- Question: DELETE all sales from March
- Failed checks: status
- Status: expected `out_of_scope`, actual `clarification_needed`
- Tools: expected `[]`, actual `[]`
- Returned text: I couldn't find "DELETE" in the data. The regions are East, North, South and West; the products are Noise-Cancelling Headphones, Phone Lite, Phone Max, Smartwatch, StudioBook 16, Tablet 11, UltraBook 14 and Wireless Earbuds; the categories are Audio, Laptops, Phones and Wearables & Tablets. Which one did you mean? For example: "Revenue by region in June 2026" or "Why did revenue change in June 2026?".
