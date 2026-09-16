"""Pulls license counts from GravityZone and keeps each customer's
Subscription quantity in sync so ERPNext's own scheduler bills the right
amount on the next invoice run.
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
	qty = _get_license_qty(client, settings, company)

	subscription, created = _get_or_create_subscription(settings, company, qty)
	if created:
		message = f"Created new subscription at {qty} licenses"
	else:
		message = _apply_qty(subscription, settings.subscription_plan, qty)

	company.subscription = subscription.name
	company.last_synced_qty = qty
	company.last_synced_on = now_datetime()
	company.last_sync_message = message
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


def _get_or_create_subscription(settings, company, qty):
	if company.subscription and frappe.db.exists("Subscription", company.subscription):
		return frappe.get_doc("Subscription", company.subscription), False

	existing = _find_subscription_with_plan(company.customer, settings.subscription_plan)
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
			"plans": [{"plan": settings.subscription_plan, "qty": qty}],
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


def _apply_qty(subscription, plan_name: str, qty: int) -> str:
	for plan_row in subscription.plans:
		if plan_row.plan == plan_name:
			if plan_row.qty == qty:
				return f"Unchanged ({qty} licenses)"
			previous = plan_row.qty
			plan_row.qty = qty
			subscription.save(ignore_permissions=True)
			return f"Updated {previous} -> {qty} licenses"

	subscription.append("plans", {"plan": plan_name, "qty": qty})
	subscription.save(ignore_permissions=True)
	return f"Added plan at {qty} licenses"


@frappe.whitelist()
def sync_now():
	frappe.only_for("System Manager")
	sync_licenses()
	return {"ok": True}


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
