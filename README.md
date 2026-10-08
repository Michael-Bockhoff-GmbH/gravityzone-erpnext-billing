# GravityZone Billing

A Frappe/ERPNext app that syncs license (seat) counts from **Bitdefender
GravityZone** (MSP/Partner) into a recurring-billing document in ERPNext,
so invoices scale automatically with license count — no manual invoice
editing when a customer adds or removes seats.

## How it works

1. Each ERPNext customer you bill for GravityZone seats gets a **GravityZone
   Company** record mapping their GravityZone company ID to their ERPNext
   Customer.
2. A daily scheduled job (or the **Sync Licenses Now** button) calls the
   GravityZone Licensing API for each mapped company and reads the current
   seat count.
3. It finds (or creates) that customer's recurring-billing document and sets
   its quantity to match. Two **Billing Backends** are supported — see
   [Billing backend](#billing-backend):
   - **ERPNext Subscription** (default): core ERPNext's own Subscription
     doctype, against a single, shared **Subscription Plan** (e.g.
     "Bitdefender GravityZone License", priced per seat).
   - **ALYF Simple Subscription**: [alyf-de/simple_subscription](https://github.com/alyf-de/simple_subscription),
     a lighter alternative some shops use instead, billing an **Item**
     directly with no separate Plan object.
4. The chosen backend's own scheduler generates the invoice at the end of
   each billing period using whatever quantity is currently set — so a
   seat-count change picked up by the sync before that run is reflected
   automatically, with no custom invoicing logic to maintain.

This intentionally reuses each backend's built-in recurring billing rather
than generating Sales Invoices directly (for ERPNext Subscription) — or,
for Simple Subscription, defers entirely to that app's own invoice
generation — so tax templates, payment terms, dunning, etc. all keep
working exactly as they do for any other document of that type.

**Optional: bill by product, not just by seat.** GravityZone's Licensing API
doesn't only report a flat seat count — it can break usage down per product
(base Endpoint Security, EDR, Patch Management, Full Disk Encryption, Email
Security, Exchange Protection, Sandbox Analyzer/ATI, Compliance Manager,
...). Switch License Metric to **Per-Product Monthly Usage** to bill each of
those as its own ERPNext Item/Subscription Plan line instead of one flat
per-seat line — see [Per-product billing](#per-product-billing) below.

## Install

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/Michael-Bockhoff-GmbH/gravityzone-erpnext-billing --branch main
bench --site your-site install-app gravityzone_billing
```

If you want the **ALYF Simple Subscription** billing backend, also get and
install that app (it's optional — everything above works with core ERPNext
Subscription alone):

```bash
bench get-app https://github.com/alyf-de/simple_subscription --branch version-16
bench --site your-site install-app simple_subscription
```

## Setup

1. **Create a dedicated GravityZone API key.** In GravityZone Control
   Center, go to *My Account → API keys* and create a key with access to
   only the **Network** and **Licensing** APIs (Partner tier — a
   non-partner "Cloud Solutions" account cannot list companies). Leave
   **Companies** (*Unternehmen*) off unless you want the optional address and
   contact import for new ERPNext Customers — see the security note.

   > **Security note:** GravityZone scopes API keys per whole service, with
   > no read/write split within a service. The Licensing API toggle that
   > exposes the read-only `getLicenseInfo`/`getMonthlyUsage` methods this
   > app uses *also* exposes write methods like `setMonthlySubscription`
   > that can change a company's licensing state — there is no way to get a
   > Licensing key that is read-only at the GravityZone level. This app
   > never calls those write methods, but a leaked key is only as safe as
   > the services you enabled on it. Create a **separate key for this
   > integration** with just Network + Licensing checked — leave Accounts,
   > Policies, and Reports unchecked — so a leak here can't touch anything
   > else.
   >
   > **Companies is the exception to think about:** the Companies API also
   > contains `createCompany`, `deleteCompany`, `suspendCompany` and
   > `updateCompanyDetails`. It is only needed for the contact-data import
   > (`getCompanyDetails`), and GravityZone's company data is sparse (see
   > below), so the safer default is to leave it off. Without it everything
   > else works; new Customers are then created without address/contact and
   > the result says "contact data not loaded".
   >
   > **Troubleshooting:** if **Discover Companies** or a sync fails with
   > `Invalid API key. Please generate an API key in Control Center.` (HTTP
   > 401), GravityZone doesn't recognize the key on the host this app is
   > calling. That is an authentication problem, not a missing API scope:
   > check that the key in GravityZone Settings is the one currently shown
   > in Control Center, and that **API Base URL** matches the *Access URL*
   > (Zugriffs-URL) Control Center shows next to the API keys — cloud
   > tenants can differ (e.g. `cloud.…` vs `cloudgz.…`).
   >
   > Which checkbox gates `getCompaniesList` — **Netzwerk**/Network (as the
   > Bitdefender docs suggest) or **Unternehmen**/Companies — has not yet
   > been verified against a working key. If the key is accepted but the
   > call is refused, enable the other one and retry.
2. **Create the billing Item** in ERPNext (Selling): one non-stock Item
   (e.g. "GravityZone License Seat") priced per seat. If you're using the
   **ERPNext Subscription** backend, also create a **Subscription Plan**
   using that Item — Simple Subscription doesn't use Subscription Plans at
   all, it bills the Item directly.
3. Open **GravityZone Settings** (single doctype) and fill in:
   - **API Key** and **API Base URL** — the *Access URL* shown next to the
     API keys in Control Center, plus `/v1.0/jsonrpc`. For a cloud tenant
     that is `https://cloudgz.gravityzone.bitdefender.com/api/v1.0/jsonrpc`;
     on-prem Control Center installs use their own hostname. The prefilled
     default is the cloud host above; check it against your Access URL.
   - **License Metric** — *License Info* (current allocated seats),
     *Monthly Usage* (actual monthly consumption; Bitdefender's recommended
     source for billing reconciliation), or *Per-Product Monthly Usage* (see
     [Per-product billing](#per-product-billing)).
   - **Billing Backend** — see [Billing backend](#billing-backend).
   - **Default Company**, and either the **Subscription Plan** (ERPNext
     Subscription backend) or **Item** (Simple Subscription backend)
     created above (required for License Info / Monthly Usage; ignored for
     Per-Product Monthly Usage, which uses GravityZone Product Mapping
     instead).
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
   GravityZone customer list into **GravityZone Company** records. It walks
   the whole company tree: sub-companies are recorded individually with a
   **Parent Company** link (GravityZone has no recursive listing, so each
   partner-type company is asked for its children via `getCompaniesList`'s
   `parentId`).

   **Parents and sub-companies.** GravityZone adds a sub-company's usage to
   its parent's counters — checked live: a parent with no endpoints of its
   own reported exactly its two children's seats. Billing both would count
   every seat twice. A company that gains its first sub-company is therefore
   marked **Usage Includes Sub-Companies**, and the sync bills it only for
   its **own share**: its counters minus its direct sub-companies'. A parent
   with no licenses of its own comes out at 0 (and gets no billing document
   as long as it is 0); one that has its own licenses is billed for exactly
   those. Un-tick the mark if a company's counters turn out not to include
   its sub-companies. **Exclude from Sync** is a separate, manual switch that
   skips a company entirely; it is never set automatically.

   **ERPNext Customers.** Click **Create Missing ERPNext Customers**
   (GravityZone Settings) or **Create ERPNext Customers** (top of the
   GravityZone Company list) to create one for every company that has none —
   named after the GravityZone company, linking an existing Customer with
   exactly that name instead of duplicating it. Or use **Create ERPNext
   Customer** on a single record: it is always available, and on a company
   that is already linked it asks for confirmation first, then links the
   company to a Customer named after it. The old Customer is not changed;
   the company's billing-document link is cleared because that document
   belongs to the old Customer (the next sync creates a new one for the new
   Customer, the old one is yours to cancel). A newly created Customer
   gets only a name and type "Company", plus — best effort — an **Address**
   and **Contact** from GravityZone. GravityZone's data is sparse: the
   address is a single free-text string, often empty (on the tenant this was
   checked against it held only street and number, no ZIP or city — ERPNext
   requires a city, so it is skipped and reported rather than guessed), the
   phone is free text, and a contact person exists only on partner-type
   companies. Reading it needs the **Companies / Unternehmen** checkbox on
   the API key. Customer Group, Territory and tax IDs are never filled.
   Optionally set a **Minimum Billable Seats** floor for minimum-commit
   contracts.
5. Click **Sync Licenses Now** to run the first sync immediately, or wait for
   the daily scheduled job. Each **GravityZone Company** record shows its
   last synced quantity, the linked **Subscription**, and a **Sync History**
   table logging every sync outcome (created/updated/unchanged/flagged/
   approved) for audit purposes.

### When a change is held for review

If a synced license count changes by more than the configured thresholds,
the **GravityZone Company** record is marked **Needs Review** with the new
count shown in **Pending License Count** (or, in Per-Product mode, on the
specific flagged row in **Product Lines**) — the Subscription itself is left
untouched for that line. Open the record and click **Approve Pending
Change(s)** to apply everything currently pending, or investigate first if
the jump looks wrong (e.g. a GravityZone API error rather than real growth).

## Billing backend

GravityZone Settings' **Billing Backend** picks which ERPNext-side doctype
actually gets billed:

| | ERPNext Subscription (default) | ALYF Simple Subscription |
|---|---|---|
| Billing target | **Subscription Plan** (a template: Item + price + interval) | **Item** directly, no separate Plan object |
| Document state | Draft/Active, no submit step | Must be **submitted** to generate invoices — this app submits it automatically on creation |
| Invoice generation | Core ERPNext's own Subscription scheduler | Simple Subscription's own scheduler ([alyf-de/simple_subscription](https://github.com/alyf-de/simple_subscription)) |
| Updating quantity on an existing document | Normal field update + save | Direct write to the already-submitted child row's quantity (Simple Subscription's `items` table isn't editable after submit through the normal UI/API) — see `billing_backends.py` |

They are **not interchangeable data models** — Simple Subscription has no
concept of a Subscription Plan at all. This app abstracts both behind the
same sync logic (`gravityzone_billing/billing_backends.py`), so switching
the Billing Backend setting is safe, but:

- **Fill in the right target field.** Subscription Plan for ERPNext
  Subscription, Item for Simple Subscription — both on GravityZone Settings
  (flat mode) and on each GravityZone Product Mapping row (per-product
  mode).
- **Switching backends for an already-onboarded customer creates a new
  document** under the new backend on their next sync; the old one is left
  as-is (this mirrors Simple Subscription's own stated approach for
  migrating away from core Subscription — "you have to cancel them
  manually"). It is not an automatic migration.
- Everything else — the review guard, Sync History, per-product mode,
  Discover Companies — works identically regardless of which backend is
  active.

## Per-product billing

Set GravityZone Settings' **License Metric** to **Per-Product Monthly
Usage** to bill each GravityZone product as its own ERPNext line instead of
one flat per-seat line.

1. **Create an Item per product you sell** (e.g. "GZ Endpoint Security" and
   "GZ EDR"), plus a **Subscription Plan** per Item if you're using the
   ERPNext Subscription backend (skip that for Simple Subscription, which
   bills Items directly).
2. **Fill the GravityZone Product Mapping list.** Easiest: click **Discover
   License Types** on GravityZone Settings. It reads every GravityZone
   Company's protection model and this month's usage counters, shows what
   each company holds in the **GravityZone Licenses** field on its record
   (e.g. `Model: mspSecurePlus | Subscription: Monthly | Usage: Endpoint
   Security 13, MSP Secure Plus 13`), and creates one *disabled* mapping row
   per counter. The dialog asks whether to **also add license types nobody
   uses yet** (ticked by default): GravityZone returns its full counter set
   for every company, zeros included — 33 counters on the tenant this was
   checked against, 3 of them in use — so ticking it gives you the complete
   catalogue to assign in advance, and the **Companies Using** column shows
   which ones are actually in use (un-tick it to get only those). You then
   only pick the Plan/Item per row and enable it. (Bitdefender's API has no "list of licenses" for
   monthly-subscription companies — the model and the counters *are* the
   licenses. Yearly license keys, `subscriptionType` 2, can additionally
   return an `additionalLicenses` list via `getLicenseInfo`'s
   `returnAllProducts` option; this app doesn't read that yet. **MDR**
   (Managed Detection & Response) appears under `licensedServices` as a
   status, but has no usage counter in practice — `mdrFoundationsMonthlyUsage`
   was 0 for companies with MDR active — so it can't be billed from
   counters.) Or add the rows by hand, one per product:
   - **GravityZone Usage Field** — the counter name from GravityZone's
     `getMonthlyUsagePerProductType` response. Counters seen on a live cloud
     tenant: `endpointMonthlyUsage` (base Endpoint Security),
     `edrMonthlyUsage`, `patchManagementMonthlyUsage`,
     `encryptionMonthlyUsage` (Full Disk Encryption), `emailSecurityMonthlyUsage`,
     `exchangeMonthlyUsage` (Exchange Protection), `atsMonthlyUsage`
     (Sandbox Analyzer / Advanced Threat Intelligence), `complianceMonthlyUsage`
     (Compliance Manager), plus the MSP package tiers
     `mspSecureEssentialsMonthlyUsage`, `mspSecureMonthlyUsage`,
     `mspSecurePlusMonthlyUsage`, `mspSecureExtraMonthlyUsage` and
     `aLaCarteMonthlyUsage`.

     > **Don't bill the same seat twice.** The counters overlap: on the
     > tenant this was checked against, `mspSecurePlusMonthlyUsage` equalled
     > `endpointMonthlyUsage` (every endpoint is also counted under its
     > package tier). If you sell by MSP package, map the package counters
     > (`mspSecure…`, `aLaCarte…`) and **not** `endpointMonthlyUsage` as well,
     > or each seat is invoiced once per counter. Compare a customer's
     > counters in Control Center before choosing.
     >
     > The sync enforces this for Per-Product mode: if a company has any seat
     > on an `mspSecure…` package counter (Essentials, Secure, Plus, Extra),
     > its `endpointMonthlyUsage` bills as 0, even when the Endpoint Security
     > mapping row is enabled. Companies without a package (e.g. only
     > `aLaCarte…`) keep their Endpoint Security count, so don't enable
     > both that row and `aLaCarte…` for the same customers.
   - **Subscription Plan** or **Item** — whichever matches GravityZone
     Settings' Billing Backend.
   - **Enabled** — unchecked rows are skipped by the sync.
3. Run **Sync Licenses Now**. Each mapped product gets its own row in that
   company's **Product Lines** table (own baseline, own review flag, own
   pending quantity) and its own line on the customer's *single* billing
   document — one document per customer, multiple line items on it.

**Migrating existing flat-plan customers:** map your existing per-seat
Subscription Plan/Item to whatever counter corresponds to it (see the
double-billing note above — `endpointMonthlyUsage` if you bill by plain
seat, the matching package counter if you bill by MSP package) and
existing customers keep billing on that same line under the new mode — any other product GravityZone reports for them
just gets added as a new line on their existing document. Nothing needs to
be manually migrated.

**Product-level review guard:** each product line is judged against its
*own* history independently — e.g. an EDR count jumping from 3 to 50 gets
flagged even if the customer's base Endpoint Security seats haven't changed
at all, and vice versa; a big change on one product never blocks the others
from syncing normally.

## Verifying against the real GravityZone API

Bitdefender's Partner API documentation lives behind a JS-rendered partner
portal, so `gravityzone_client.py` was first written from published guides
and then corrected against a live cloud tenant. What has been **checked
against a real API key** (cloud MSP, `cloudgz` host, read-only calls):

- Host and auth: the **API Base URL** must be the Access URL Control Center
  shows (`https://cloudgz.gravityzone.bitdefender.com/api`) plus
  `/v1.0/jsonrpc`; a valid key sent to another host is answered with
  `Invalid API key`.
- `getCompaniesList` (`network`): takes **no parameters** and returns a plain
  list of `{id, name}` — `page`/`perPage` are rejected.
- `getLicenseInfo` (`licensing`): seat count is `usedSlots` (`totalSlots` is
  `null` when the license has no fixed slot count) — there is no
  `usedLicenses` field.
- `getMonthlyUsage` / `getMonthlyUsagePerProductType`: `targetMonth` must be
  `mm/yyyy`. The first returns a flat counter dict, the second
  `{"usages": [{...all counters..., "productType": N}, ...]}`.

Still **not** verified: which API-key checkbox gates `getCompaniesList`
(Netzwerk/Network vs. Unternehmen/Companies — the call worked with both
enabled), on-prem Control Center hosts, and tenants with several product
types per company. To re-check on your own tenant, call the methods above
directly (curl/Postman) with your key and compare against the client.

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
Sync History logging, plus per-product mode: multi-line creation, one
product flagging independently of the others, and approving only the
flagged lines — for both the ERPNext Subscription and, if
`simple_subscription` is installed, ALYF Simple Subscription backends) is
covered by
`gravityzone_billing/doctype/gravityzone_company/test_gravityzone_company.py`.
Note: running `bench run-tests --app gravityzone_billing` also triggers
Frappe's automatic loading of test fixtures for every linked doctype
(including Customer); on some benches with other customization apps
installed, unrelated pre-existing fixture bugs in those apps (e.g. a
duplicate default Price List) can surface here. That is not specific to this
app — run the module path above to test this app in isolation.

## License

mit
