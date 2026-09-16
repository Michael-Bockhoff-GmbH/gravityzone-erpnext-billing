# GravityZone Billing

A Frappe/ERPNext app that syncs license (seat) counts from **Bitdefender
GravityZone** (MSP/Partner) into ERPNext's native **Subscription** doctype,
so recurring invoices scale automatically with license count — no manual
invoice editing when a customer adds or removes seats.

## How it works

1. Each ERPNext customer you bill for GravityZone seats gets a **GravityZone
   Company** record mapping their GravityZone company ID to their ERPNext
   Customer.
2. A daily scheduled job (or the **Sync Licenses Now** button) calls the
   GravityZone Licensing API for each mapped company and reads the current
   seat count.
3. It finds (or creates) that customer's **Subscription** against a single,
   shared **Subscription Plan** (e.g. "Bitdefender GravityZone License",
   priced per seat) and sets the plan's quantity to match.
4. ERPNext's own scheduler generates the invoice at the end of each billing
   period using whatever quantity is currently set — so a seat-count change
   picked up by the sync before that run is reflected automatically, with no
   custom invoicing logic to maintain.

This intentionally reuses ERPNext's built-in recurring billing rather than
generating Sales Invoices directly, so tax templates, payment terms, dunning,
etc. all keep working exactly as they do for any other ERPNext Subscription.

## Install

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/Michael-Bockhoff-GmbH/gravityzone-erpnext-billing --branch main
bench --site your-site install-app gravityzone_billing
```

## Setup

1. **Create a dedicated GravityZone API key.** In GravityZone Control
   Center, go to *My Account → API keys* and create a key with access to
   only the **Network** and **Licensing** APIs (Partner tier — a
   non-partner "Cloud Solutions" account cannot list companies).

   > **Security note:** GravityZone scopes API keys per whole service, with
   > no read/write split within a service. The Licensing API toggle that
   > exposes the read-only `getLicenseInfo`/`getMonthlyUsage` methods this
   > app uses *also* exposes write methods like `setMonthlySubscription`
   > that can change a company's licensing state — there is no way to get a
   > Licensing key that is read-only at the GravityZone level. This app
   > never calls those write methods, but a leaked key is only as safe as
   > the services you enabled on it. Create a **separate key for this
   > integration** with just Network + Licensing checked — leave Companies,
   > Accounts, Policies, and Reports unchecked — so a leak here can't be
   > used to create/delete/suspend companies or touch anything else.
2. **Create the billing Item and Subscription Plan in ERPNext** (Selling /
   Accounts): one non-stock Item (e.g. "GravityZone License Seat") priced
   per seat, and one **Subscription Plan** using that Item.
3. Open **GravityZone Settings** (single doctype) and fill in:
   - **API Key** and **API Base URL** (defaults to the Cloud MSP endpoint;
     see `.env`-style comments in the doctype for on-prem Control Center
     installs).
   - **License Metric** — *License Info* (current allocated seats) or
     *Monthly Usage* (actual monthly consumption; Bitdefender's recommended
     source for billing reconciliation).
   - **Default Company** and the **Subscription Plan** created above.
   - **Automatic Review Thresholds** — a synced change is held for manual
     review only when it exceeds **both** the percentage and seat-count
     thresholds here (default: 50% and 5 seats), not either one alone. A
     500-seat customer picking up 6 more devices is +6 seats but only
     +1.2% — nowhere near 50% — so it's applied automatically like any
     normal change; a 1-seat customer going to 2 seats is +100% but only
     +1 seat — under the 5-seat floor — so that's automatic too. Only a
     change large in *both* absolute and relative terms (e.g. 7 → 50
     seats) gets held, which is what actually looks like an API glitch or
     a data problem rather than organic growth.
4. Click **Discover Companies** on GravityZone Settings to pull your
   GravityZone customer list into **GravityZone Company** records, then open
   each one and set its **ERPNext Customer** (and, optionally, a **Minimum
   Billable Seats** floor for minimum-commit contracts).
5. Click **Sync Licenses Now** to run the first sync immediately, or wait for
   the daily scheduled job. Each **GravityZone Company** record shows its
   last synced quantity, the linked **Subscription**, and a **Sync History**
   table logging every sync outcome (created/updated/unchanged/flagged/
   approved) for audit purposes.

### When a change is held for review

If a synced license count changes by more than the configured thresholds,
the **GravityZone Company** record is marked **Needs Review** with the new
count shown in **Pending License Count** — the Subscription itself is left
untouched. Open the record and click **Approve Pending Change** to apply it,
or investigate first if the jump looks wrong (e.g. a GravityZone API error
rather than a real seat increase).

## Verifying against the real GravityZone API

Bitdefender's Partner API documentation lives behind a JS-rendered partner
portal, so `gravityzone_billing/gravityzone_client.py` was written from
Bitdefender's published API guides and known method/parameter names rather
than a captured live response. Before relying on this in production:

1. Generate a real API key and enable verbose logging (or use `curl`/Postman)
   to call `getCompaniesList` (Network service) and `getLicenseInfo` /
   `getMonthlyUsage` (Licensing service) directly.
2. Compare the response shape to what `gravityzone_client.py` expects
   (`items`/`page`/`pagesCount` for lists; `usedLicenses`/`additionalLicenses`
   for license info). Adjust the small amount of field-name handling in that
   file if your tenant's response differs.

## Development

```bash
cd apps/gravityzone_billing
pre-commit install
```

Run the dependency-free unit tests (GravityZone JSON-RPC client, including
rate-limit backoff, and the large-jump review guard's threshold logic):

```bash
bench --site your-site set-config allow_tests true
bench --site your-site run-tests --module gravityzone_billing.tests.test_gravityzone_client
bench --site your-site run-tests --module gravityzone_billing.tests.test_sync_guard
```

The full sync flow (create Subscription → increase quantity → no-op when
unchanged → minimum-seat floor → large-jump review flag → manual approval →
Sync History logging) is covered by
`gravityzone_billing/doctype/gravityzone_company/test_gravityzone_company.py`.
Note: running `bench run-tests --app gravityzone_billing` also triggers
Frappe's automatic loading of test fixtures for every linked doctype
(including Customer); on some benches with other customization apps
installed, unrelated pre-existing fixture bugs in those apps (e.g. a
duplicate default Price List) can surface here. That is not specific to this
app — run the module path above to test this app in isolation.

## License

mit
