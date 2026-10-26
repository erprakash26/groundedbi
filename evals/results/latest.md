# GroundedBI evaluation report

- Commit: `7aa1cb4`
- Seed: 42 (data 2026-01, 6 months)
- Provider: rule_based
- Run at: 2026-10-26T22:29:32+00:00

Rates are over the questions where each check applies (shown as passed/total). Arguments are only checked when the tool list is correct; grounding only for answered questions. A confident wrong answer is an answered, grounded response to a question that still failed.

## Split: dev (gated by --fail-under)

| Group | n | Pass | Status | Tools | Arguments | Grounding | Confident wrong | p50 ms | p95 ms | max ms | Avg tools |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **overall** | 21 | 100% (21/21) | 100% (21/21) | 100% (21/21) | 100% (14/14) | 100% (14/14) | 0 | 7.1 | 14.6 | 18.7 | 1.57 |
| breakdown_category | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 7.1 | 7.1 | 7.1 | 2.0 |
| breakdown_product | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 8.5 | 8.5 | 8.5 | 2.0 |
| breakdown_region | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 6.9 | 6.9 | 6.9 | 2.0 |
| change_drivers | 3 | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 0 | 14.6 | 18.7 | 18.7 | 2.0 |
| clarify_no_intent | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 4.0 | 4.0 | 4.0 | 1.0 |
| clarify_relative_time | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 4.1 | 4.1 | 4.1 | 1.0 |
| clarify_unknown_name | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 4.3 | 4.3 | 4.3 | 1.0 |
| comparison | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 6.5 | 6.5 | 6.5 | 2.0 |
| false_premise | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 9.1 | 9.1 | 9.1 | 2.0 |
| monthly | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 100% (2/2) | 100% (2/2) | 0 | 6.7 | 8.8 | 8.8 | 2.0 |
| out_of_scope_forecast | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 1.6 | 1.6 | 1.6 | 0.0 |
| out_of_scope_injection | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 1.7 | 1.7 | 1.7 | 0.0 |
| out_of_scope_metric | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 1.6 | 1.6 | 1.6 | 0.0 |
| out_of_scope_unrelated | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 4.3 | 4.3 | 4.3 | 1.0 |
| partial_coverage | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 7.1 | 7.1 | 7.1 | 2.0 |
| top_n | 3 | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (3/3) | 0 | 10.8 | 13.4 | 13.4 | 2.33 |

## Split: heldout (measured only, never gated)

| Group | n | Pass | Status | Tools | Arguments | Grounding | Confident wrong | p50 ms | p95 ms | max ms | Avg tools |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **overall** | 10 | 30% (3/10) | 60% (6/10) | 70% (7/10) | 100% (3/3) | 100% (5/5) | 2 | 5.7 | 14.3 | 14.3 | 1.5 |
| false_premise | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 10.2 | 10.2 | 10.2 | 2.0 |
| no_data | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 5.7 | 5.7 | 5.7 | 2.0 |
| normal | 3 | 0% (0/3) | 67% (2/3) | 0% (0/3) | n/a | 100% (2/2) | 2 | 7.5 | 14.3 | 14.3 | 1.67 |
| out_of_scope | 2 | 0% (0/2) | 0% (0/2) | 100% (2/2) | n/a | n/a | 0 | 4.2 | 4.4 | 4.4 | 1.0 |
| typos | 1 | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) | 0 | 10.3 | 10.3 | 10.3 | 2.0 |
| vague | 1 | 0% (0/1) | 0% (0/1) | 100% (1/1) | n/a | n/a | 0 | 4.6 | 4.6 | 4.6 | 1.0 |
| vague_tricky | 1 | 0% (0/1) | 100% (1/1) | 100% (1/1) | n/a | n/a | 0 | 4.9 | 4.9 | 4.9 | 1.0 |

## Failures (7)

### heldout-01 (heldout, normal) **(confident wrong)**

- Question: what happened to sales in march?
- Failed checks: tools, must_contain
- Status: expected `answered`, actual `answered`
- Tools: expected `["get_change_drivers"]`, actual `["get_revenue_by_month"]`
- Missing from text: `["StudioBook 16 / West", "$314,032"]`
- Returned text: Revenue in March 2026 was $1,511,790.

### heldout-02 (heldout, normal)

- Question: How much revenue did the West region bring in during Q2?
- Failed checks: status, tools, must_contain
- Status: expected `answered`, actual `clarification_needed`
- Tools: expected `["get_revenue_breakdown"]`, actual `[]`
- Missing from text: `["$2,021,557"]`
- Returned text: I couldn't find "during" in the data. The regions are East, North, South and West; the products are Noise-Cancelling Headphones, Phone Lite, Phone Max, Smartwatch, StudioBook 16, Tablet 11, UltraBook 14 and Wireless Earbuds; the categories are Audio, Laptops, Phones and Wearables & Tablets. Which one did you mean? For example: "Revenue by region in June 2026" or "Why did revenue change in June 2026?".

### heldout-03 (heldout, normal) **(confident wrong)**

- Question: Which product sold best in January 2026?
- Failed checks: tools, must_contain
- Status: expected `answered`, actual `answered`
- Tools: expected `["get_top_products"]`, actual `["get_revenue_by_month"]`
- Missing from text: `["StudioBook 16", "$507,297"]`
- Returned text: Revenue in January 2026 was $1,780,189.

### heldout-05 (heldout, vague)

- Question: how are we doing?
- Failed checks: status
- Status: expected `clarification_needed`, actual `out_of_scope`
- Tools: expected `[]`, actual `[]`
- Returned text: That question is outside what I can help with; I only answer questions about the sales data. I can explain what drove a revenue change, compare two periods, show monthly revenue, break revenue down by product, region or category, and list the top products.

### heldout-06 (heldout, vague_tricky)

- Question: Show me sales for the Northeast region
- Failed checks: must_contain
- Status: expected `clarification_needed`, actual `clarification_needed`
- Tools: expected `[]`, actual `[]`
- Missing from text: `["Northeast"]`
- Returned text: I'm not sure what you'd like to see. What would you like to know? For example: "Why did revenue change in June 2026?", "Top 5 products in Q2 2026" or "Revenue by region in June 2026".

### heldout-07 (heldout, out_of_scope)

- Question: What is the average order value by customer segment?
- Failed checks: status
- Status: expected `out_of_scope`, actual `clarification_needed`
- Tools: expected `[]`, actual `[]`
- Returned text: Customer data isn't in the data, but I can show revenue for June 2026. Would you like that? For example: "Revenue in June 2026" or "Revenue by region in June 2026".

### heldout-08 (heldout, out_of_scope)

- Question: Write me a poem about our Q1 numbers
- Failed checks: status
- Status: expected `out_of_scope`, actual `clarification_needed`
- Tools: expected `[]`, actual `[]`
- Returned text: I'm not sure what you'd like to see. What would you like to know? For example: "Why did revenue change in June 2026?", "Top 5 products in Q2 2026" or "Revenue by region in June 2026".
