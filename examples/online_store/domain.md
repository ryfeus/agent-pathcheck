# Online store domain: entities, use cases and questions

A single running domain for the blog and the repo examples. Everyone understands orders, customers and products, so readers learn the entities once and every later example builds on them.

---

## 1. Entities

### Core entities

| Entity | Key fields | Notes |
|---|---|---|
| **Customer** | `customer_id`, `signup_date`, `region`, `segment` (consumer / business), `marketing_opt_in` | Who buys |
| **Order** | `order_id`, `customer_id`, `placed_at`, `storefront` (prod / test), `channel` (web / app / marketplace), `status` | One purchase |
| **OrderLine** | `order_id`, `product_id`, `quantity`, `unit_price_cents`, `discount_cents` | What was bought |
| **Product** | `product_id`, `category_id`, `supplier_id`, `status` (active / discontinued), `list_price_cents`, `unit_cost_cents` | What's sold |
| **Category** | `category_id`, `name`, `status` | Apparel, Electronics, Outdoor… |
| **Shipment** | `order_id`, `shipped_at`, `delivered_at`, `carrier` | Fulfilment |
| **Return** | `return_id`, `order_id`, `product_id`, `returned_at`, `refund_cents`, `reason` | Money flowing back |
| **InventorySnapshot** | `product_id`, `warehouse_id`, `date`, `on_hand`, `reorder_point` | Stock levels |
| **Supplier** | `supplier_id`, `status`, `lead_time_days`, `region` | Who restocks |
| **Promotion** | `promo_id`, `category_id`, `discount_pct`, `starts_at`, `ends_at` | Discounts |

### Relationships

- A Customer **places** Orders.
- An Order **contains** OrderLines, and each OrderLine **refers to** one Product.
- A Product **belongs to** a Category and **is sourced from** a Supplier.
- An Order **has** a Shipment.
- A Return **reverses** a specific OrderLine. That line's Order must belong to the customer requesting the refund.
- An InventorySnapshot **describes** one Product in one warehouse on one day.
- A Promotion **applies to** a Category during a time window.

### Measures (the semantic layer)

| Measure | Definition | Common trap |
|---|---|---|
| **Gross revenue** | Σ quantity × unit price | Confused with net revenue |
| **Net revenue** | Gross revenue − discounts − refunds | Refunds counted in the month of the *order* instead of the month of the *return* (or the other way round) |
| **Margin** | Net revenue − Σ quantity × unit cost | Unit cost missing for new products |
| **Active customer** | At least one prod order in the last *N* days | *N* differs between teams (90 vs 60) |
| **Days of sales history** | Distinct days with sales data for *this* product | Borrowing history from a sibling variant |
| **Days of cover** | On-hand stock ÷ average daily units sold | Divide-by-zero for products that don't sell |
| **Return rate** | Returned units ÷ sold units for the same period | Mismatched periods for the numerator and denominator |
| **Late delivery** | Delivered more than *SLA* days after shipping | Undelivered orders silently excluded |

### Invariants (attached to entities and true for every policy)

- Money is in integer cents. Refunds are never larger than the original line amount.
- Test-storefront orders never count toward any measure.
- A Return refers to an OrderLine of an Order owned by the same Customer.
- `returned_at` comes on or after `delivered_at`.
- Month boundaries use the store's timezone, not UTC.
- Discontinued products are never restocked or promoted.

### Decisions (parameters with an owner, not facts)

| Parameter | Value | Owner |
|---|---|---|
| Active-customer window | 90 days | CRM |
| Refund window | 30 days after delivery | Support |
| Auto-approve refund limit | $500 | Support |
| Minimum sales history for restock | 30 days | Merchandising |
| Minimum monthly sales to restock | $50 (added later from a correction) | Merchandising |
| Restock list size | Top 5 | Merchandising |
| Gold loyalty tier | ≥ $2,000 net revenue in 12 months | CRM |
| Delivery SLA | 5 days | Ops |

---

## 2. Internal users

| User | Cares about |
|---|---|
| **Finance analyst** | Revenue, refunds, margin, month-end numbers |
| **Merchandiser** | What to restock, mark down or discontinue |
| **Ops / inventory planner** | Stock levels, suppliers, late deliveries |
| **Support lead** | Refund eligibility, repeat returners |
| **CRM / marketing manager** | Loyalty tiers, win-back lists, promotion results |
| **Exec** | "What happened and what should we do?" |

---

## 3. Use cases, from simple to complex

Each level adds one new difficulty. It also maps to a section of the blog.

### Level 1: Scoped lookup

**Question:** *"What was net revenue for the production store in October?"*

- **What goes wrong:** test-storefront orders get included, gross revenue is used instead of net, or "October" is cut in UTC instead of store time.
- **What the policy does:** the request type only allows `storefront="prod"`, `metric="net_revenue"` and the month named in the task. A request for anything else fails before any query runs. The store-timezone rule lives on the Order entity, so every query inherits it.
- **Blog section:** *From output validation to path validation* (wrong slice).

### Level 2: Aggregate backed by evidence

**Question:** *"How many orders shipped last week?"*

- **What goes wrong:** the agent answers without querying, or the query fails, the agent catches the error and returns a plausible number.
- **What the policy does:** the count must equal the number of shipments in a validated, committed result. A failed query adds nothing to the evidence, so the claim has nothing behind it.
- **Blog section:** *A valid schema, a wrong answer* (claiming without looking).

### Level 3: Comparison across periods

**Question:** *"How did net revenue by category change from September to October?"*

| Category | September | October | Change |
|---|---|---|---|
| Apparel | $120,000 | $145,000 | +$25,000 |
| Electronics | $90,000 | $108,000 | +$18,000 |
| Home | $60,000 | $68,000 | +$8,000 |
| Beauty *(new in October)* | — | $6,000 | +$6,000 |
| Books | $30,000 | $35,000 | +$5,000 |
| Outdoor *(discontinued in October)* | $3,000 | — | −$3,000 |
| **Total** | **$303,000** | **$362,000** | **+$59,000** |

- **What goes wrong:** an agent that loops over October's categories drops Outdoor and reports **+$62,000**. Another gets the total right but invents the breakdown.
- **What the policy does:** every change is recomputed from both months, with a missing category counted as zero. The order must be exact (largest change first, ties by name), and the total must be exact.
- **Blog section:** *A valid schema, a wrong answer* (the running example).

### Level 4: Joins on the same entity

**Question:** *"Is the refund request on order #1042 within policy?"*

- **What goes wrong:** the agent checks the 30-day window against a *different* order's delivery date, or approves a return for an order that belongs to another customer.
- **What the policy does:** the chain Return → OrderLine → Order → Customer must stay on one customer throughout. The window is measured from *this* order's `delivered_at`. The refund can't exceed the line amount, and anything over $500 needs a human.
- **Blog section:** *Pydantic models are the ontology* (relationship rules).

### Level 5: Eligibility plus ranking

**Question:** *"Which 5 products should we restock this week?"*

A product is eligible only if all of these hold:
- the product is active and its supplier is active
- on-hand stock is below the reorder point
- it has at least 30 days of sales history *for this product*
- margin is positive
- days of cover are shorter than the supplier's lead time

Eligible products are ranked by projected lost revenue, ties broken by product ID, one entry per product.

| Product | Lost revenue | Eligible? | Why not |
|---|---|---|---|
| P-104 Desk lamp | $6,000 | ✗ | Supplier inactive |
| P-101 Rain jacket | $4,200 | ✓ | |
| P-102 Trail shoes | $3,900 | ✓ | |
| P-103 Phone case | $3,000 | ✗ | Only 21 days of history (a sibling variant, P-113, has 90) |
| P-105 Yoga mat | $2,500 | ✗ | Discontinued |
| P-107 Backpack | $2,100 | ✓ | |
| P-108 Headphones | $2,100 | ✓ | Tie with P-107, so ranked after it by ID |
| P-106 Water bottle | $1,500 | ✗ | Negative margin |
| P-109 Notebook | $800 | ✓ | |
| P-110 Mug | $650 | ✓ | Ranked sixth, so not in the top 5 |

**Correct answer:** P-101, P-102, P-107, P-108, P-109, totalling **$13,100**.

**What goes wrong:**
- leading with P-104, the biggest number, even though it's ineligible
- counting P-103 by borrowing P-113's history
- listing P-108 before P-107
- skipping P-109
- answering "nothing to restock" when the inventory query never ran

**What the policy does:** each proposal is checked against the evidence, and the whole plan is recomputed with the same rules.

**Blog section:** *Pydantic models are the ontology* (decisions that span steps).

### Level 6: Concepts shared across policies

**Questions:** *"Which customers move to Gold tier this month?"* and *"Which customers are at risk of churning?"*

- **What goes wrong:** both depend on "active customer". CRM changes the window from 90 days to 60. The loyalty policy gets the update; the churn policy keeps 90. The two answers now disagree about the same customers.
- **What the policy does:** "active customer" is defined once on the Customer entity. Both policies inherit it, so changing it once updates both, and you can list every affected policy.
- **Blog section:** *From three policies to a hundred* (drift) and *Pydantic models are the ontology*.

### Level 7: Knowing when not to answer

**Questions:**
- *"What's the margin on P-120?"* (unit cost is missing because the product is new)
- *"What was October's return rate?"* (the returns query hit its row limit)

- **What goes wrong:** the agent guesses the cost from similar products, or computes a rate from partial data. A different agent refuses whenever the task gets hard.
- **What the policy does:** refusal is accepted only when a read the policy *requires* was blocked, such as missing cost on the exact product or a truncated returns query for the exact month. A blocked optional read, or a query that later recovered, doesn't justify a refusal.
- **Blog section:** *A spectrum of enforcement* (refusal as a typed claim).

### Level 8: A correction becomes a rule

**Reviewer correction:** *"Don't restock anything selling under $50/month."*

- The rule enters as **documented** (a note), becomes **checked** (a validator rejects restocking P-109 Notebook at $40/month), then **tested** (a $49 case must fail and a $50 case must pass).
- **Impact on past answers:** last week's restock list changes. P-109 drops out, P-110 Mug ($60/month) moves in, and the total becomes $12,950.
- **Blog section:** *A spectrum of enforcement* (the correction story).

### Level 9: Composite questions

**Question:** *"Why did net revenue in the West region fall last month, and which promotions should we pause?"*

This breaks down into checkable parts:
1. A **comparison** of net revenue by category for the West region, month over month (Level 3 shape).
2. A **ranking** of promotions whose discount exceeded the category margin while they ran (Level 5 shape).
3. A **verified join** between the two: only promotions on categories that declined.

- The *why* stays as narrative and is labeled unverified. The numbers and the pause list are checked.
- **Blog section:** *The models are the semantic layer* (complex questions).

### Level 10: Questions no policy covers yet

**Question:** *"Which suppliers caused the most late deliveries to Gold customers last quarter?"*

- No human wrote this policy. The planner composes it from existing parts:
  - the measure: late deliveries
  - grouped by: supplier
  - filtered by: Gold tier, inherited from the loyalty policy
  - the shape: top-k
- It's shown with the answer in plain language: *"Top 5 suppliers by late deliveries (more than 5 days after shipping) on orders from Gold-tier customers, Q3, production store only."*
- The planner can add filters but can't drop the inherited rules: production storefront only, store timezone, and the same-customer chain.
- **Blog section:** *Where this goes: policies the planner composes*.

---

## 4. Questions internal users ask

Each question is tagged with its level and **claim shape**. Most questions fall into six shapes:
- **lookup**
- **aggregate**
- **compare** (across periods)
- **check one** (is this one entity eligible?)
- **top-k** (eligibility plus ranking)
- **abstain**

| User | Question | Level | Shape |
|---|---|---|---|
| Finance | What was net revenue for the production store in October? | 1 | lookup |
| Finance | How much did we refund last month, and how much of it was over $500? | 2 | aggregate |
| Finance | How did net revenue by category change from September to October? | 3 | compare |
| Finance | How did gross margin by category change from Q2 to Q3? | 3 | compare |
| Finance | Which orders were refunded for more than was paid? (should be none) | 2 | aggregate |
| Merchandiser | Which 5 products should we restock this week? | 5 | top-k |
| Merchandiser | Which products should we mark down? (more than 90 days of cover, active, not on promotion, still profitable after markdown) | 5 | top-k |
| Merchandiser | Which categories lost the most active customers this quarter? | 6 | compare |
| Ops | Which orders are late right now? | 2 | aggregate |
| Ops | Which warehouses have fewer than 7 days of cover on best-sellers? | 5 | top-k |
| Ops | Which suppliers missed their lead time most often last quarter? | 5 | top-k |
| Support | Is the refund request on order #1042 within policy? | 4 | check one |
| Support | Which customers returned more than 3 orders in the last 60 days? | 6 | top-k |
| CRM | Which customers move to Gold tier this month? | 6 | top-k |
| CRM | Build a win-back list: customers inactive 90–180 days, at least 2 past orders, opted in, top 100 by lifetime net revenue | 6 | top-k |
| CRM | Did the October Apparel promotion pay for itself? | 9 | compare + check one |
| Any | What's the margin on P-120? (unit cost missing) | 7 | abstain |
| Exec | Why did West-region revenue fall, and which promotions should we pause? | 9 | composite |
| Exec | Which suppliers caused the most late deliveries to Gold customers? | 10 | planner-composed |

That's 19 questions in six shapes. This backs up the blog's claim that a small library of claim shapes covers most real questions.

---

## 5. Shared concepts across policies (for the "hundred policies" section)

| Policy | Prod storefront only | Net revenue | Active customer | Same-customer chain | Days of sales history | Store timezone |
|---|---|---|---|---|---|---|
| Restock | ✓ | ✓ | | | ✓ | ✓ |
| Markdown | ✓ | ✓ | | | ✓ | ✓ |
| Refund | ✓ | | | ✓ | | ✓ |
| Loyalty tiers | ✓ | ✓ | ✓ | | | ✓ |
| Churn / win-back | ✓ | ✓ | ✓ | | | ✓ |
| Fraud review | ✓ | | | ✓ | | ✓ |
| Supplier scorecard | ✓ | | | | | ✓ |
| Sales commissions | ✓ | ✓ | | ✓ | | ✓ |

**The drift story:** "active customer" goes from 90 days to 60. Written as separate policies, loyalty tiers get the update and churn / win-back keep a stale copy. Defined on the Customer entity, both inherit the change.

---

## 6. The enforcement spectrum in this domain

| Level | Example rule |
|---|---|
| **Documented** | "Prefer suppliers in the same region as the warehouse" |
| **Guided** | "Mention when a category is new or discontinued in the comparison" |
| **Checked** | A refund must fall within 30 days of *this order's* delivery |
| **Tested** | The 30-day boundary is kept as cases: day 30 passes, day 31 fails |

---

## 7. Mapping to blog sections

| Blog section | Store example |
|---|---|
| A valid schema, a wrong answer | Level 3: Outdoor dropped, +$62k vs +$59k |
| From output validation to path validation | Levels 1–2: storefront, metric and month literals; ledger-backed counts |
| Why not just a policy engine? | Cedar can check "refund ≤ $500"; it can't derive the 30-day window from this order's delivery date or recompute the restock ranking |
| From three policies to a hundred | Section 5: "active customer" drift |
| Pydantic models are the ontology | Levels 4–6: same-customer chain, restock eligibility, shared concepts |
| A spectrum of enforcement | Section 6, Levels 7–8 |
| The models are the semantic layer | Measures table (net vs gross, store timezone), Level 9 |
| Where this goes | Level 10 |
