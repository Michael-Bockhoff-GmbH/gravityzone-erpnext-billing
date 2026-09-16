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

from datetime import date

import frappe
from frappe.utils import now_datetime

from gravityzone_billing.billing_backends import get_backend, target_for
from gravityzone_billing.gravityzone_client import GravityZoneClient


def sync_licenses():
	settings = frappe.get_single("GravityZone Settings")
	if not settings.sync_enabled:
		return

	client = GravityZoneClient(api_key=settings.get_password("api_key"), base_url=settings.base_url)

	for row in frappe.get_all("GravityZone Company", pluck="name"):
		try:
			sync_one_company(client, settings, row)
		except Exception:
			frappe.log_error(
				title=f"GravityZone sync failed: {row}",
				message=frappe.get_traceback(),
			)


def sync_one_company(client: GravityZoneClient, settings, company_name: str):
	company = frappe.get_doc("GravityZone Company", company_name)
	if settings.license_metric == "Per-Product Monthly Usage":
		_sync_company_per_product(client, settings, company)
	else:
		_sync_company_flat(client, settings, company)


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
		target_month = date.today().strftime("%Y-%m")
		usage = client.get_monthly_usage(company.gz_company_id, target_month)
		qty = int(usage.get("endpoints", usage.get("usedLicenses", 0)))
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

	target_month = date.today().strftime("%Y-%m")
	usages = client.get_monthly_usage_per_product_type(company.gz_company_id, target_month)

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
	sync_licenses()
	return {"ok": True}


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


@frappe.whitelist()
def discover_companies():
	"""Pull the MSP company list from GravityZone and create/refresh
	GravityZone Company records (without a Customer assigned yet, for the
	user to fill in).
	"""
	frappe.only_for("System Manager")
	settings = frappe.get_single("GravityZone Settings")
	client = GravityZoneClient(api_key=settings.get_password("api_key"), base_url=settings.base_url)

	created, updated = 0, 0
	for company in client.get_companies_list():
		if frappe.db.exists("GravityZone Company", company.id):
			frappe.db.set_value("GravityZone Company", company.id, "gz_company_name", company.name)
			updated += 1
		else:
			frappe.get_doc(
				{
					"doctype": "GravityZone Company",
					"gz_company_id": company.id,
					"gz_company_name": company.name,
				}
			).insert(ignore_permissions=True, ignore_mandatory=True)
			created += 1

	frappe.db.commit()
	return {"created": created, "updated": updated}
