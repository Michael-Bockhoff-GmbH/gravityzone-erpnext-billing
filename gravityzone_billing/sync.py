"""Pulls license counts from GravityZone and keeps each customer's
billing document quantity in sync so the recurring-billing backend bills
the right amount on the next invoice run.

Two billing modes, chosen by GravityZone Settings' License Metric:

- License Info / Monthly Usage: one flat per-seat line via Settings'
  Subscription Plan / Item (``_sync_company_flat``).
- Per-Product Monthly Usage: each GravityZone product (EDR, Patch
  Management, Endpoint Security, ...) bills as its own line, per the
  GravityZone Product Mapping list, each tracked independently on the
  company's Product Lines table (``_sync_company_per_product``).

Two billing backends, chosen by GravityZone Settings' Billing Backend (see
billing_backends.py): core ERPNext Subscription, or ALYF Simple
Subscription. sync.py only ever talks to the backend through
billing_backends.get_backend()/target_for() — it never assumes which one is
active.

In both modes, a change that looks anomalous (see GravityZone Settings'
review thresholds) is held for manual approval instead of applied
automatically, and every sync outcome is appended to the company's Sync
History for audit purposes.
"""

import json
import re
from datetime import date

import frappe
from frappe.utils import now_datetime

from gravityzone_billing.billing_backends import SIMPLE_SUBSCRIPTION, get_backend, target_for
from gravityzone_billing.gravityzone_client import GravityZoneClient, GravityZoneError


def sync_licenses() -> dict:
	"""Sync every GravityZone Company. Returns ``{"synced", "skipped", "failed"}``
	counts so callers (the Sync Licenses Now button) can say what happened.
	"""
	result = {"synced": 0, "skipped": 0, "failed": 0}
	settings = frappe.get_single("GravityZone Settings")
	if not settings.sync_enabled:
		return result

	_check_settings(settings)
	client = GravityZoneClient(api_key=settings.get_password("api_key"), base_url=settings.base_url)

	for row in frappe.get_all("GravityZone Company", pluck="name"):
		try:
			result[sync_one_company(client, settings, row)] += 1
		except Exception:
			result["failed"] += 1
			frappe.log_error(
				title=f"GravityZone sync failed: {row}",
				message=frappe.get_traceback(),
			)
	return result


def _check_settings(settings):
	"""Fail fast with a plain message instead of letting every company hit a
	cryptic ERPNext "Value missing for Subscription Plan" error.
	"""
	problems = []
	if not settings.default_company:
		problems.append("Default Company is not set")

	if settings.license_metric == "Per-Product Monthly Usage":
		mappings = frappe.get_all(
			"GravityZone Product Mapping", filters={"enabled": 1}, fields=["name", "subscription_plan", "item"]
		)
		if not mappings:
			problems.append("no enabled GravityZone Product Mapping exists")
		unassigned = [m.name for m in mappings if not target_for(settings, m)]
		if unassigned:
			field = "Item" if settings.billing_backend == SIMPLE_SUBSCRIPTION else "Subscription Plan"
			problems.append(f"enabled Product Mapping without a {field}: {', '.join(unassigned)}")
	elif not target_for(settings, settings):
		field = "Item" if settings.billing_backend == SIMPLE_SUBSCRIPTION else "Subscription Plan"
		problems.append(f"{field} is not set")

	if problems:
		frappe.throw("GravityZone sync is not configured: " + "; ".join(problems) + ".")


def sync_one_company(client: GravityZoneClient, settings, company_name: str) -> str:
	"""Returns ``"synced"``, or ``"skipped"`` for a company with no ERPNext Customer yet."""
	company = frappe.get_doc("GravityZone Company", company_name)

	skip_reason = None
	if company.exclude_from_sync:
		skip_reason = "Skipped: excluded from sync (its sub-companies are billed individually)"
	elif not company.customer:
		skip_reason = "Skipped: no ERPNext Customer assigned yet"

	if skip_reason:
		# set_value, not save(): a record from Discover Companies has no Customer yet and
		# would fail the mandatory-field check.
		frappe.db.set_value(
			"GravityZone Company",
			company_name,
			{"last_synced_on": now_datetime(), "last_sync_message": skip_reason},
		)
		frappe.db.commit()
		return "skipped"

	if settings.license_metric == "Per-Product Monthly Usage":
		_sync_company_per_product(client, settings, company)
	else:
		_sync_company_flat(client, settings, company)
	return "synced"


# --- Flat mode: one plan/item, one quantity, for the whole company ----------


def _sync_company_flat(client: GravityZoneClient, settings, company):
	backend = get_backend(settings)
	target = target_for(settings, settings)

	qty = _get_license_qty(client, settings, company)
	doc = _existing_billing_doc(backend, company)
	baseline = backend.get_qty(doc, target) if doc else None

	if baseline is not None and _is_large_jump(baseline, qty, settings):
		message = _flag_message(baseline, qty, settings)
		company.needs_review = 1
		company.pending_qty = qty
		company.last_synced_on = now_datetime()
		company.last_sync_message = message
		_append_history(company, baseline, qty, "Flagged for Review", message)
		company.save(ignore_permissions=True)
		frappe.db.commit()
		return

	if doc is None:
		doc = backend.find_existing_with_target(company.customer, target)
	created = doc is None
	if created:
		doc = backend.create(company.customer, settings.default_company, settings, target, qty)
		outcome, message = "Created", f"Created new subscription at {qty} licenses"
	else:
		outcome, message = backend.set_qty(doc, target, qty)

	company.billing_doctype = backend.doctype
	company.subscription = doc.name
	company.last_synced_qty = qty
	company.last_synced_on = now_datetime()
	company.last_sync_message = message
	if company.needs_review:
		company.needs_review = 0
		company.pending_qty = None
	_append_history(company, baseline, qty, outcome, message)
	company.save(ignore_permissions=True)
	frappe.db.commit()


def _get_license_qty(client: GravityZoneClient, settings, company) -> int:
	if settings.license_metric == "Monthly Usage":
		usage = client.get_monthly_usage(company.gz_company_id, date.today().strftime("%m/%Y"))
		qty = usage.get("endpointMonthlyUsage")
		if qty is None:
			raise GravityZoneError(
				None, f"getMonthlyUsage returned no endpointMonthlyUsage; got: {sorted(usage)}"
			)
		qty = int(qty)
	else:
		info = client.get_license_info(company.gz_company_id)
		qty = info.used_licenses

	return max(qty, company.min_qty or 0)


def _existing_billing_doc(backend, company):
	"""The company's current billing document, if it exists and belongs to
	the currently active backend. A company left over from a different
	backend (e.g. after switching GravityZone Settings' Billing Backend) is
	treated as having none yet — see billing_backends.py's module docstring.
	"""
	if company.subscription and company.billing_doctype == backend.doctype and frappe.db.exists(
		backend.doctype, company.subscription
	):
		return frappe.get_doc(backend.doctype, company.subscription)
	return None


# --- Per-product mode: one line per mapped GravityZone product --------------


def _sync_company_per_product(client: GravityZoneClient, settings, company):
	backend = get_backend(settings)
	mappings = frappe.get_all(
		"GravityZone Product Mapping",
		filters={"enabled": 1},
		fields=["name", "gz_usage_field", "subscription_plan", "item", "label"],
	)
	if not mappings:
		company.last_synced_on = now_datetime()
		company.last_sync_message = "No enabled GravityZone Product Mappings configured"
		company.save(ignore_permissions=True)
		frappe.db.commit()
		return

	usages = client.get_monthly_usage_per_product_type(company.gz_company_id, date.today().strftime("%m/%Y"))

	doc = _existing_billing_doc(backend, company)

	summary = []
	any_flagged = False
	# A brand-new billing document can't always be created empty (ERPNext's
	# Subscription validation breaks on an empty plans table), so
	# accepted-but-not-yet-applied lines are batched here and the document is
	# created once, seeded with all of them together.
	pending_lines = []

	for mapping in mappings:
		target = target_for(settings, mapping)
		qty = int(usages.get(mapping.gz_usage_field, 0) or 0)
		row = _get_or_add_product_line(company, mapping)
		baseline = row.last_synced_qty if row.last_synced_on else None

		if baseline is not None and _is_large_jump(baseline, qty, settings):
			message = _flag_message(baseline, qty, settings)
			row.needs_review = 1
			row.pending_qty = qty
			any_flagged = True
			_append_history(company, baseline, qty, "Flagged for Review", message, product=mapping.label)
			summary.append(f"{mapping.label}: needs review")
			continue

		if doc is None:
			pending_lines.append((target, qty))
			outcome, message = "Created", f"Added plan at {qty} licenses"
		else:
			outcome, message = backend.set_qty(doc, target, qty)

		row.last_synced_qty = qty
		row.last_synced_on = now_datetime()
		if row.needs_review:
			row.needs_review = 0
			row.pending_qty = None
		_append_history(company, baseline, qty, outcome, message, product=mapping.label)
		summary.append(f"{mapping.label}: {outcome.lower()}")

	if doc is None and pending_lines:
		(first_target, first_qty), *rest = pending_lines
		doc = backend.create(company.customer, settings.default_company, settings, first_target, first_qty)
		for target, qty in rest:
			backend.set_qty(doc, target, qty)

	if doc is not None:
		company.billing_doctype = backend.doctype
		company.subscription = doc.name

	company.needs_review = 1 if any_flagged else 0
	company.last_synced_qty = sum(row.last_synced_qty or 0 for row in company.get("product_lines") or [])
	company.last_synced_on = now_datetime()
	company.last_sync_message = "; ".join(summary)
	company.save(ignore_permissions=True)
	frappe.db.commit()


def _get_or_add_product_line(company, mapping):
	for row in company.get("product_lines") or []:
		if row.gz_usage_field == mapping.gz_usage_field:
			row.label = mapping.label
			row.subscription_plan = mapping.subscription_plan
			row.item = mapping.item
			return row
	return company.append(
		"product_lines",
		{
			"gz_usage_field": mapping.gz_usage_field,
			"label": mapping.label,
			"subscription_plan": mapping.subscription_plan,
			"item": mapping.item,
		},
	)


# --- Shared helpers -----------------------------------------------------------


def _is_large_jump(baseline: int, qty: int, settings) -> bool:
	delta = qty - baseline
	if abs(delta) <= (settings.review_threshold_min_seats or 0):
		return False

	pct_change = (abs(delta) / baseline * 100) if baseline > 0 else (100.0 if qty else 0.0)
	return pct_change > (settings.review_threshold_percent or 0)


def _flag_message(baseline: int, qty: int, settings) -> str:
	delta = int(qty - baseline)
	pct_change = (abs(delta) / baseline * 100) if baseline > 0 else 100.0
	return (
		f"Needs review: {baseline} -> {qty} licenses ({delta:+d}, {pct_change:.0f}%) exceeds the "
		f"{settings.review_threshold_percent:g}%/{settings.review_threshold_min_seats}-seat review threshold"
	)


def _append_history(company, previous_qty, new_qty, outcome: str, message: str, product: str = ""):
	company.append(
		"sync_history",
		{
			"timestamp": now_datetime(),
			"product": product,
			"previous_qty": previous_qty,
			"new_qty": new_qty,
			"outcome": outcome,
			"message": message,
		},
	)


@frappe.whitelist()
def sync_now():
	frappe.only_for("System Manager")
	return sync_licenses()


@frappe.whitelist()
def approve_pending_change(company_name: str):
	"""Apply a flat-mode change that was held for review, after a human has
	looked at it. For per-product mode, use approve_all_pending instead.
	"""
	frappe.only_for("System Manager")
	settings = frappe.get_single("GravityZone Settings")
	backend = get_backend(settings)
	target = target_for(settings, settings)
	company = frappe.get_doc("GravityZone Company", company_name)

	if not company.needs_review or company.pending_qty is None:
		frappe.throw("This company has no pending change awaiting approval.")

	qty = company.pending_qty
	baseline = company.last_synced_qty

	doc = _existing_billing_doc(backend, company) or backend.find_existing_with_target(company.customer, target)
	created = doc is None
	if created:
		doc = backend.create(company.customer, settings.default_company, settings, target, qty)
		message = f"Created new subscription at {qty} licenses (manually approved)"
	else:
		_, message = backend.set_qty(doc, target, qty)
		message = f"Approved by {frappe.session.user}: {message}"

	company.billing_doctype = backend.doctype
	company.subscription = doc.name
	company.last_synced_qty = qty
	company.last_synced_on = now_datetime()
	company.last_sync_message = message
	company.needs_review = 0
	company.pending_qty = None
	_append_history(company, baseline, qty, "Approved", message)
	company.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist()
def approve_all_pending(company_name: str):
	"""Apply every pending change on a company — the flat-mode pending
	quantity if any, plus every flagged Product Line — after a human has
	reviewed them. Works for both billing modes and both backends.
	"""
	frappe.only_for("System Manager")
	settings = frappe.get_single("GravityZone Settings")
	backend = get_backend(settings)
	company = frappe.get_doc("GravityZone Company", company_name)
	approved = 0

	if company.needs_review and company.pending_qty is not None and not company.get("product_lines"):
		approve_pending_change(company_name=company_name)
		return {"approved": 1}

	doc = _existing_billing_doc(backend, company)

	for row in company.get("product_lines") or []:
		if not row.needs_review or row.pending_qty is None:
			continue
		baseline = row.last_synced_qty
		qty = row.pending_qty
		target = target_for(settings, row)

		if doc is None:
			doc = backend.create(company.customer, settings.default_company, settings, target, qty)
			company.billing_doctype = backend.doctype
			company.subscription = doc.name
			message = f"Approved by {frappe.session.user}: Created new subscription at {qty} licenses"
		else:
			_, message = backend.set_qty(doc, target, qty)
			message = f"Approved by {frappe.session.user}: {message}"
		row.last_synced_qty = qty
		row.last_synced_on = now_datetime()
		row.needs_review = 0
		row.pending_qty = None
		_append_history(company, baseline, qty, "Approved", message, product=row.label)
		approved += 1

	company.needs_review = 1 if any(r.needs_review for r in company.get("product_lines") or []) else 0
	if approved:
		company.last_synced_qty = sum(row.last_synced_qty or 0 for row in company.get("product_lines") or [])
		company.last_synced_on = now_datetime()
	company.save(ignore_permissions=True)
	frappe.db.commit()
	return {"approved": approved}


def _collect_company_tree(client) -> list:
	"""Every company under the API key's company, as ``(company, parent_id)`` pairs,
	parents before their children. ``parent_id`` is None for the top level.

	GravityZone has no recursive listing, so each company is asked for its
	children. A customer-type company (``type`` 1) can't have any and answers
	error -32602 for ``parentId`` — that means "leaf", not a failure; any other
	error is real and propagates.
	"""
	found = []
	seen = set()
	queue = [(company, None) for company in client.get_companies_list()]
	while queue:
		company, parent_id = queue.pop(0)
		if company.id in seen:
			continue
		seen.add(company.id)
		found.append((company, parent_id))
		try:
			children = client.get_companies_list(parent_id=company.id)
		except GravityZoneError as error:
			if error.code != -32602:
				raise
			children = []
		queue.extend((child, company.id) for child in children)
	return found


@frappe.whitelist()
def discover_companies():
	"""Pull the whole GravityZone company tree and create/refresh GravityZone
	Company records (without a Customer assigned yet, for the user to fill in).

	Sub-companies are recorded individually with a link to their parent. A
	parent that gains its first sub-company here is marked Exclude from Sync,
	because its usage counters appear to be the sum of its children's and
	syncing both would bill every seat twice. That mark is only set at that
	moment, so un-ticking it later sticks.
	"""
	frappe.only_for("System Manager")
	settings = frappe.get_single("GravityZone Settings")
	client = GravityZoneClient(api_key=settings.get_password("api_key"), base_url=settings.base_url)

	created = updated = sub_companies = excluded_parents = 0
	for company, parent_id in _collect_company_tree(client):
		if parent_id:
			sub_companies += 1

		if frappe.db.exists("GravityZone Company", company.id):
			values = {"gz_company_name": company.name}
			if parent_id:
				values["parent_company"] = parent_id
			frappe.db.set_value("GravityZone Company", company.id, values)
			updated += 1
			continue

		first_child = bool(parent_id) and not frappe.db.exists("GravityZone Company", {"parent_company": parent_id})
		frappe.get_doc(
			{
				"doctype": "GravityZone Company",
				"gz_company_id": company.id,
				"gz_company_name": company.name,
				"parent_company": parent_id,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		created += 1

		if first_child:
			frappe.db.set_value("GravityZone Company", parent_id, "exclude_from_sync", 1)
			excluded_parents += 1

	frappe.db.commit()
	return {
		"created": created,
		"updated": updated,
		"sub_companies": sub_companies,
		"excluded_parents": excluded_parents,
	}


# Meanings per Bitdefender's getLicenseInfo documentation.
SUBSCRIPTION_TYPES = {
	1: "Trial",
	2: "Licensed",
	3: "Monthly",
	4: "Monthly license trial",
	5: "Monthly subscription trial",
	6: "FRAT",
}
PRODUCT_TYPES = {0: "Endpoint Security", 3: "EDR", 5: "PHASR"}
_ACRONYMS = {"msp", "edr", "ats", "ai", "spm", "easm", "sve", "vdi", "vs", "xdr", "mdr", "phasr"}


def _counter_label(field: str) -> str:
	"""Readable name for a GravityZone usage counter, e.g. ``mspSecurePlusMonthlyUsage``
	-> ``MSP Secure Plus``.
	"""
	if field == "endpointMonthlyUsage":
		return "Endpoint Security"
	name = re.sub(r"(MonthlyUsage|Usage)$", "", field)
	words = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name)
	return " ".join(w.upper() if w.lower() in _ACRONYMS else w[:1].upper() + w[1:] for w in words)


def _license_summary(info: dict, usage: dict) -> str:
	"""One line describing what a company holds: protection model, subscription
	type, any additional product types, and this month's non-zero counters.
	"""
	subscription = SUBSCRIPTION_TYPES.get(info.get("subscriptionType"), info.get("subscriptionType"))
	parts = [f"Model: {info.get('assignedProtectionModel') or 'n/a'}", f"Subscription: {subscription}"]

	extra = [PRODUCT_TYPES.get(t, str(t)) for t in info.get("additionalProductTypes") or []]
	if extra:
		parts.append("Additional products: " + ", ".join(extra))

	counters = ", ".join(f"{_counter_label(f)} {v}" for f, v in sorted(usage.items()))
	if counters:
		parts.append("Usage: " + counters)
	return " | ".join(parts)


@frappe.whitelist()
def discover_license_types():
	"""Read every GravityZone Company's license model and this month's usage
	counters, write a readable summary onto each company, and create a
	(disabled, still unassigned) GravityZone Product Mapping row for every
	counter that is non-zero for at least one company — so each license type
	only needs an ERPNext Item / Subscription Plan picked and enabling.
	"""
	frappe.only_for("System Manager")
	settings = frappe.get_single("GravityZone Settings")
	companies = frappe.get_all("GravityZone Company", fields=["name", "gz_company_id"])
	if not companies:
		frappe.throw("There are no GravityZone Company records yet — run Discover Companies first.")

	client = GravityZoneClient(api_key=settings.get_password("api_key"), base_url=settings.base_url)
	month = date.today().strftime("%m/%Y")

	companies_per_counter = {}
	for company in companies:
		info = client.get_license_info(company.gz_company_id).raw
		usage = {
			field: value
			for field, value in client.get_monthly_usage_per_product_type(company.gz_company_id, month).items()
			if value
		}
		for field in usage:
			companies_per_counter[field] = companies_per_counter.get(field, 0) + 1
		frappe.db.set_value("GravityZone Company", company.name, "licenses_summary", _license_summary(info, usage))

	created = 0
	for field in sorted(companies_per_counter):
		if frappe.db.exists("GravityZone Product Mapping", field):
			continue
		frappe.get_doc(
			{
				"doctype": "GravityZone Product Mapping",
				"gz_usage_field": field,
				"label": _counter_label(field),
				"enabled": 0,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		created += 1

	frappe.db.commit()
	return {
		"companies": len(companies),
		"types": len(companies_per_counter),
		"created": created,
		"counters": companies_per_counter,
	}


@frappe.whitelist()
def create_missing_erpnext_customers():
	"""create_erpnext_customers for every GravityZone Company that has no
	Customer yet and isn't excluded from sync.
	"""
	frappe.only_for("System Manager")
	names = frappe.get_all(
		"GravityZone Company",
		filters={"customer": ["is", "not set"], "exclude_from_sync": 0},
		pluck="name",
	)
	return create_erpnext_customers(json.dumps(names))


@frappe.whitelist()
def create_erpnext_customers(company_names):
	"""Give each GravityZone Company that has no ERPNext Customer yet one,
	named after the GravityZone company. A Customer with exactly that name is
	linked instead of creating a duplicate. Companies that already have a
	Customer are left alone.

	The new Customer gets only a name and type "Company" — Customer Group,
	Territory, tax IDs and addresses are not known from GravityZone and are
	left for you to fill in. All-or-nothing: if one fails, none are saved.
	"""
	frappe.only_for("System Manager")
	created = linked = skipped = 0

	for name in frappe.parse_json(company_names):
		company = frappe.get_doc("GravityZone Company", name)
		if company.customer:
			skipped += 1
			continue

		customer_name = (company.gz_company_name or company.gz_company_id).strip()
		customer = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
		if customer:
			linked += 1
		else:
			customer = (
				frappe.get_doc(
					{"doctype": "Customer", "customer_name": customer_name, "customer_type": "Company"}
				)
				.insert(ignore_permissions=True)
				.name
			)
			created += 1

		company.customer = customer
		company.save(ignore_permissions=True)

	frappe.db.commit()
	return {"created": created, "linked": linked, "skipped": skipped}
