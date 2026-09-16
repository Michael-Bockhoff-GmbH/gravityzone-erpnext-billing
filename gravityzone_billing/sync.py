"""Pulls license counts from GravityZone and keeps each customer's
Subscription quantity in sync so ERPNext's own scheduler bills the right
amount on the next invoice run.

Two billing modes, chosen by GravityZone Settings' License Metric:

- License Info / Monthly Usage: one flat per-seat line via Settings'
  Subscription Plan (``_sync_company_flat``).
- Per-Product Monthly Usage: each GravityZone product (EDR, Patch
  Management, Endpoint Security, ...) bills as its own line, per the
  GravityZone Product Mapping list, each tracked independently on the
  company's Product Lines table (``_sync_company_per_product``).

In both modes, a change that looks anomalous (see GravityZone Settings'
review thresholds) is held for manual approval instead of applied
automatically, and every sync outcome is appended to the company's Sync
History for audit purposes.
"""

from datetime import date

import frappe
from frappe.utils import now_datetime, today

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


# --- Flat mode: one plan, one quantity, for the whole company ---------------


def _sync_company_flat(client: GravityZoneClient, settings, company):
	qty = _get_license_qty(client, settings, company)
	baseline = _get_current_plan_qty(company, settings.subscription_plan)

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

	subscription, created = _get_or_create_subscription(settings, company, settings.subscription_plan, qty)
	if created:
		outcome, message = "Created", f"Created new subscription at {qty} licenses"
	else:
		outcome, message = _apply_qty(subscription, settings.subscription_plan, qty)

	company.subscription = subscription.name
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


def _get_current_plan_qty(company, plan_name):
	"""The quantity currently on the customer's Subscription for this plan, if any.

	Read from the live Subscription (not our cached last_synced_qty) so a jump
	is judged against reality even if someone edited the Subscription by hand.
	"""
	subscription = None
	if company.subscription and frappe.db.exists("Subscription", company.subscription):
		subscription = frappe.get_doc("Subscription", company.subscription)
	else:
		subscription = _find_subscription_with_plan(company.customer, plan_name)

	if not subscription:
		return None

	for row in subscription.plans:
		if row.plan == plan_name:
			return row.qty
	return None


# --- Per-product mode: one line per mapped GravityZone product --------------


def _sync_company_per_product(client: GravityZoneClient, settings, company):
	mappings = frappe.get_all(
		"GravityZone Product Mapping",
		filters={"enabled": 1},
		fields=["name", "gz_usage_field", "subscription_plan", "label"],
	)
	if not mappings:
		company.last_synced_on = now_datetime()
		company.last_sync_message = "No enabled GravityZone Product Mappings configured"
		company.save(ignore_permissions=True)
		frappe.db.commit()
		return

	target_month = date.today().strftime("%Y-%m")
	usages = client.get_monthly_usage_per_product_type(company.gz_company_id, target_month)

	subscription = None
	if company.subscription and frappe.db.exists("Subscription", company.subscription):
		subscription = frappe.get_doc("Subscription", company.subscription)
	else:
		subscription = _find_any_subscription_for_customer(company.customer)

	summary = []
	any_flagged = False
	# A brand-new Subscription can't be created with an empty plans table (ERPNext's
	# own validation breaks on it), so accepted-but-not-yet-applied plans are batched
	# here and the Subscription is created once, seeded with all of them together.
	pending_plans = []

	for mapping in mappings:
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

		if subscription is None:
			pending_plans.append({"plan": mapping.subscription_plan, "qty": qty})
			outcome, message = "Created", f"Added plan at {qty} licenses"
		else:
			outcome, message = _apply_qty(subscription, mapping.subscription_plan, qty)

		row.last_synced_qty = qty
		row.last_synced_on = now_datetime()
		if row.needs_review:
			row.needs_review = 0
			row.pending_qty = None
		_append_history(company, baseline, qty, outcome, message, product=mapping.label)
		summary.append(f"{mapping.label}: {outcome.lower()}")

	if subscription is None and pending_plans:
		subscription = frappe.get_doc(
			{
				"doctype": "Subscription",
				"party_type": "Customer",
				"party": company.customer,
				"company": settings.default_company,
				"start_date": today(),
				"generate_invoice_at": "End of the current subscription period",
				"plans": pending_plans,
			}
		)
		subscription.insert(ignore_permissions=True)

	if subscription is not None:
		company.subscription = subscription.name

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
			return row
	return company.append(
		"product_lines",
		{
			"gz_usage_field": mapping.gz_usage_field,
			"label": mapping.label,
			"subscription_plan": mapping.subscription_plan,
		},
	)


def _ensure_subscription_shell(settings, company, initial_plan: str, initial_qty: int):
	"""Find or create the one Subscription that holds all of this customer's
	product lines (each line is a plan row on the same Subscription).

	ERPNext's Subscription validation breaks on an empty plans table, so a
	freshly created one is always seeded with the given plan/qty rather than
	created empty.
	"""
	if company.subscription and frappe.db.exists("Subscription", company.subscription):
		return frappe.get_doc("Subscription", company.subscription)

	existing = _find_any_subscription_for_customer(company.customer)
	if existing:
		return existing

	subscription = frappe.get_doc(
		{
			"doctype": "Subscription",
			"party_type": "Customer",
			"party": company.customer,
			"company": settings.default_company,
			"start_date": today(),
			"generate_invoice_at": "End of the current subscription period",
			"plans": [{"plan": initial_plan, "qty": initial_qty}],
		}
	)
	subscription.insert(ignore_permissions=True)
	return subscription


def _find_any_subscription_for_customer(customer: str):
	name = frappe.db.get_value("Subscription", {"party_type": "Customer", "party": customer}, "name")
	return frappe.get_doc("Subscription", name) if name else None


# --- Shared helpers -----------------------------------------------------------


def _is_large_jump(baseline: int, qty: int, settings) -> bool:
	delta = qty - baseline
	if abs(delta) <= (settings.review_threshold_min_seats or 0):
		return False

	pct_change = (abs(delta) / baseline * 100) if baseline > 0 else (100.0 if qty else 0.0)
	return pct_change > (settings.review_threshold_percent or 0)


def _flag_message(baseline: int, qty: int, settings) -> str:
	delta = qty - baseline
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


def _get_or_create_subscription(settings, company, plan_name, qty):
	if company.subscription and frappe.db.exists("Subscription", company.subscription):
		return frappe.get_doc("Subscription", company.subscription), False

	existing = _find_subscription_with_plan(company.customer, plan_name)
	if existing:
		return existing, False

	subscription = frappe.get_doc(
		{
			"doctype": "Subscription",
			"party_type": "Customer",
			"party": company.customer,
			"company": settings.default_company,
			"start_date": today(),
			"generate_invoice_at": "End of the current subscription period",
			"plans": [{"plan": plan_name, "qty": qty}],
		}
	)
	subscription.insert(ignore_permissions=True)
	return subscription, True


def _find_subscription_with_plan(customer: str, plan_name: str):
	for name in frappe.get_all(
		"Subscription",
		filters={"party_type": "Customer", "party": customer},
		pluck="name",
	):
		subscription = frappe.get_doc("Subscription", name)
		if any(p.plan == plan_name for p in subscription.plans):
			return subscription
	return None


def _apply_qty(subscription, plan_name: str, qty: int) -> tuple[str, str]:
	for plan_row in subscription.plans:
		if plan_row.plan == plan_name:
			if plan_row.qty == qty:
				return "Unchanged", f"Unchanged ({qty} licenses)"
			previous = plan_row.qty
			plan_row.qty = qty
			subscription.save(ignore_permissions=True)
			return "Updated", f"Updated {previous} -> {qty} licenses"

	subscription.append("plans", {"plan": plan_name, "qty": qty})
	subscription.save(ignore_permissions=True)
	return "Updated", f"Added plan at {qty} licenses"


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
	company = frappe.get_doc("GravityZone Company", company_name)

	if not company.needs_review or company.pending_qty is None:
		frappe.throw("This company has no pending change awaiting approval.")

	qty = company.pending_qty
	baseline = company.last_synced_qty

	subscription, created = _get_or_create_subscription(settings, company, settings.subscription_plan, qty)
	if created:
		message = f"Created new subscription at {qty} licenses (manually approved)"
	else:
		_, message = _apply_qty(subscription, settings.subscription_plan, qty)
		message = f"Approved by {frappe.session.user}: {message}"

	company.subscription = subscription.name
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
	reviewed them. Works for both billing modes.
	"""
	frappe.only_for("System Manager")
	settings = frappe.get_single("GravityZone Settings")
	company = frappe.get_doc("GravityZone Company", company_name)
	approved = 0

	if company.needs_review and company.pending_qty is not None and not company.get("product_lines"):
		approve_pending_change(company_name=company_name)
		return {"approved": 1}

	subscription = None
	if company.subscription and frappe.db.exists("Subscription", company.subscription):
		subscription = frappe.get_doc("Subscription", company.subscription)

	for row in company.get("product_lines") or []:
		if not row.needs_review or row.pending_qty is None:
			continue
		baseline = row.last_synced_qty
		qty = row.pending_qty

		if subscription is None:
			subscription = _ensure_subscription_shell(settings, company, row.subscription_plan, qty)
			company.subscription = subscription.name
			message = f"Approved by {frappe.session.user}: Created new subscription at {qty} licenses"
		else:
			_, message = _apply_qty(subscription, row.subscription_plan, qty)
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
