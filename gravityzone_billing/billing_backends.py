"""Two ERPNext-side recurring-billing backends this app can drive, selected
by GravityZone Settings' Billing Backend field:

- **ERPNext Subscription** (default): core ERPNext's Subscription doctype,
  referencing a Subscription Plan (which carries the Item and price).
- **ALYF Simple Subscription** (github.com/alyf-de/simple_subscription): a
  lighter alternative some shops use instead. It has no separate "Plan"
  object — Items are referenced directly with a quantity — and it's a
  *submittable* document: it must be submitted (docstatus=1) before its own
  daily scheduler will generate invoices from it, and it generates Sales
  Invoices itself rather than relying on core ERPNext's Subscription
  scheduler.

Both backends expose the same four operations (find_existing_with_target,
create, get_qty, set_qty) so sync.py never needs to know which one is
active. "target" means a Subscription Plan name for the ERPNext backend, or
an Item code for the Simple Subscription backend — see
GravityZone Settings/GravityZone Product Mapping's paired
subscription_plan/item fields.
"""

import frappe
from frappe.utils import today

ERPNEXT_SUBSCRIPTION = "ERPNext Subscription"
SIMPLE_SUBSCRIPTION = "ALYF Simple Subscription"


def get_backend(settings):
	if settings.billing_backend == SIMPLE_SUBSCRIPTION:
		return SimpleSubscriptionBackend()
	return ERPNextSubscriptionBackend()


def target_for(settings, row) -> str:
	"""The Subscription Plan name or Item code a mapping/settings row bills
	against, depending on the active backend. ``row`` is GravityZone Settings
	itself (flat mode) or a GravityZone Product Mapping row (per-product mode)
	— both carry the same subscription_plan/item field pair.
	"""
	if settings.billing_backend == SIMPLE_SUBSCRIPTION:
		return row.item
	return row.subscription_plan


class ERPNextSubscriptionBackend:
	doctype = "Subscription"

	def find_existing_with_target(self, customer: str, target: str):
		for name in frappe.get_all(
			"Subscription", filters={"party_type": "Customer", "party": customer}, pluck="name"
		):
			doc = frappe.get_doc("Subscription", name)
			if any(row.plan == target for row in doc.plans):
				return doc
		return None

	def create(self, customer: str, company: str, settings, initial_target: str, initial_qty: int):
		doc = frappe.get_doc(
			{
				"doctype": "Subscription",
				"party_type": "Customer",
				"party": customer,
				"company": company,
				"start_date": today(),
				"generate_invoice_at": "End of the current subscription period",
				"plans": [{"plan": initial_target, "qty": initial_qty}],
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def get_qty(self, doc, target: str):
		for row in doc.plans:
			if row.plan == target:
				return row.qty
		return None

	def set_qty(self, doc, target: str, qty: int) -> tuple[str, str]:
		for row in doc.plans:
			if row.plan == target:
				if row.qty == qty:
					return "Unchanged", f"Unchanged ({qty} licenses)"
				previous = row.qty
				row.qty = qty
				doc.save(ignore_permissions=True)
				return "Updated", f"Updated {previous} -> {qty} licenses"

		doc.append("plans", {"plan": target, "qty": qty})
		doc.save(ignore_permissions=True)
		return "Updated", f"Added plan at {qty} licenses"


class SimpleSubscriptionBackend:
	doctype = "Simple Subscription"

	def find_existing_with_target(self, customer: str, target: str):
		for name in frappe.get_all(
			"Simple Subscription", filters={"customer": customer, "docstatus": 1}, pluck="name"
		):
			doc = frappe.get_doc("Simple Subscription", name)
			if any(row.item == target for row in doc.items):
				return doc
		return None

	def create(self, customer: str, company: str, settings, initial_target: str, initial_qty: int):
		doc = frappe.get_doc(
			{
				"doctype": "Simple Subscription",
				"company": company,
				"customer": customer,
				"start_date": today(),
				"period_type": settings.simple_subscription_period_type or "calendar months",
				"billing_time": settings.simple_subscription_billing_time or "after end of period",
				"frequency": settings.simple_subscription_frequency or "Monthly",
				"items": [{"item": initial_target, "qty": initial_qty}],
			}
		)
		doc.insert(ignore_permissions=True)
		# Simple Subscription only generates invoices once submitted.
		doc.submit()
		return doc

	def get_qty(self, doc, target: str):
		# Simple Subscription Item's qty is a Float (it supports fractional
		# billing periods elsewhere in that app); GravityZone seat counts are
		# always whole, so normalize here rather than leak a float to callers
		# that assume int (e.g. the review guard's "+d" formatting).
		for row in doc.items:
			if row.item == target:
				return int(row.qty)
		return None

	def set_qty(self, doc, target: str, qty: int) -> tuple[str, str]:
		for row in doc.items:
			if row.item == target:
				if int(row.qty) == qty:
					return "Unchanged", f"Unchanged ({qty} licenses)"
				previous = int(row.qty)
				# `items` isn't marked allow_on_submit on this doctype, so a
				# normal doc.save() would raise once submitted. Update the
				# already-submitted child row's value directly instead — the
				# same bypass Simple Subscription's own maintainers use for
				# any post-submit adjustment (see its "duplicate a disabled
				# subscription to change values" alternative in the README,
				# which we deliberately avoid since it would break our own
				# tracking of a stable document per customer/product).
				frappe.db.set_value("Simple Subscription Item", row.name, "qty", qty)
				return "Updated", f"Updated {previous} -> {qty} licenses"

		new_row = frappe.get_doc(
			{
				"doctype": "Simple Subscription Item",
				"parenttype": "Simple Subscription",
				"parentfield": "items",
				"parent": doc.name,
				"idx": len(doc.items) + 1,
				"item": target,
				"qty": qty,
			}
		)
		new_row.insert(ignore_permissions=True)
		return "Updated", f"Added item at {qty} licenses"
