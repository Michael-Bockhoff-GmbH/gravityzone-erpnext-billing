# Copyright (c) 2026, Michael Bockhoff GmbH and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from gravityzone_billing import sync as sync_module
from gravityzone_billing.gravityzone_client import LicenseInfo
from gravityzone_billing.sync import approve_pending_change

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


def _mock_license_info(qty):
	return patch.object(
		sync_module.GravityZoneClient,
		"get_license_info",
		return_value=LicenseInfo("gz-co-1", qty, None, {}),
	)


class IntegrationTestGravityZoneCompany(IntegrationTestCase):
	"""
	Exercises the full GravityZone -> Subscription sync flow against a real
	(test) site: create, quantity increase, no-op idempotency, the minimum
	seat floor, the large-jump review guard (including that a big account's
	small relative change is NOT flagged), manual approval, and Sync History.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.customer = frappe.db.get_value("Customer", {}, "name") or cls._make_customer()
		cls.company = frappe.db.get_value("Company", {}, "name")

		if not frappe.db.exists("Item", "GZ-TEST-SEAT"):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": "GZ-TEST-SEAT",
					"item_name": "GravityZone Test Seat",
					"item_group": frappe.db.get_value("Item Group", {}, "name") or "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 0,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Subscription Plan", "GZ Test Plan"):
			frappe.get_doc(
				{
					"doctype": "Subscription Plan",
					"plan_name": "GZ Test Plan",
					"item": "GZ-TEST-SEAT",
					"price_determination": "Fixed Rate",
					"cost": 2.5,
					"currency": frappe.db.get_default("currency") or "USD",
					"billing_interval": "Month",
					"billing_interval_count": 1,
				}
			).insert(ignore_permissions=True)

	@classmethod
	def _make_customer(cls):
		doc = frappe.get_doc({"doctype": "Customer", "customer_name": "GZ Test Customer"})
		doc.insert(ignore_permissions=True)
		return doc.name

	def setUp(self):
		settings = frappe.get_single("GravityZone Settings")
		settings.api_key = "dummy"
		settings.base_url = "https://cloud.gravityzone.bitdefender.com/api/v1.0/jsonrpc"
		settings.license_metric = "License Info"
		settings.subscription_plan = "GZ Test Plan"
		settings.default_company = self.company
		settings.sync_enabled = 1
		settings.review_threshold_percent = 50
		settings.review_threshold_min_seats = 5
		settings.save(ignore_permissions=True)

		if frappe.db.exists("GravityZone Company", "gz-co-1"):
			frappe.delete_doc("GravityZone Company", "gz-co-1", force=True, ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "GravityZone Company",
				"gz_company_id": "gz-co-1",
				"gz_company_name": "Test Co",
				"customer": self.customer,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		doc = frappe.get_doc("GravityZone Company", "gz-co-1")
		if doc.subscription and frappe.db.exists("Subscription", doc.subscription):
			frappe.delete_doc("Subscription", doc.subscription, force=True, ignore_permissions=True)
		frappe.delete_doc("GravityZone Company", "gz-co-1", force=True, ignore_permissions=True)

	def test_creates_subscription_on_first_sync(self):
		with _mock_license_info(5):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-1")
		self.assertIn("Created", doc.last_sync_message)
		self.assertEqual(doc.last_synced_qty, 5)

		subscription = frappe.get_doc("Subscription", doc.subscription)
		self.assertEqual(subscription.plans[0].qty, 5)

	def test_updates_qty_when_license_count_changes(self):
		with _mock_license_info(5):
			sync_module.sync_licenses()
		with _mock_license_info(8):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-1")
		self.assertIn("Updated", doc.last_sync_message)
		self.assertEqual(doc.last_synced_qty, 8)

		subscription = frappe.get_doc("Subscription", doc.subscription)
		self.assertEqual(subscription.plans[0].qty, 8)

	def test_unchanged_when_license_count_same(self):
		with _mock_license_info(5):
			sync_module.sync_licenses()
		with _mock_license_info(5):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-1")
		self.assertIn("Unchanged", doc.last_sync_message)

	def test_min_qty_floor_applies(self):
		doc = frappe.get_doc("GravityZone Company", "gz-co-1")
		doc.min_qty = 3
		doc.save(ignore_permissions=True)

		with _mock_license_info(1):
			sync_module.sync_licenses()

		doc.reload()
		self.assertEqual(doc.last_synced_qty, 3)

	def test_large_jump_is_flagged_and_subscription_left_untouched(self):
		with _mock_license_info(7):
			sync_module.sync_licenses()
		with _mock_license_info(50):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-1")
		self.assertTrue(doc.needs_review)
		self.assertEqual(doc.pending_qty, 50)
		self.assertEqual(doc.last_synced_qty, 7)

		subscription = frappe.get_doc("Subscription", doc.subscription)
		self.assertEqual(subscription.plans[0].qty, 7)
		self.assertEqual(doc.sync_history[-1].outcome, "Flagged for Review")

	def test_large_company_small_relative_change_is_not_flagged(self):
		with _mock_license_info(500):
			sync_module.sync_licenses()
		with _mock_license_info(506):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-1")
		self.assertFalse(doc.needs_review)
		self.assertEqual(doc.last_synced_qty, 506)

	def test_approve_pending_change_applies_qty_and_clears_flag(self):
		with _mock_license_info(7):
			sync_module.sync_licenses()
		with _mock_license_info(50):
			sync_module.sync_licenses()

		approve_pending_change(company_name="gz-co-1")

		doc = frappe.get_doc("GravityZone Company", "gz-co-1")
		self.assertFalse(doc.needs_review)
		self.assertIsNone(doc.pending_qty)
		self.assertEqual(doc.last_synced_qty, 50)

		subscription = frappe.get_doc("Subscription", doc.subscription)
		self.assertEqual(subscription.plans[0].qty, 50)
		self.assertEqual(doc.sync_history[-1].outcome, "Approved")

	def test_sync_history_records_every_outcome(self):
		with _mock_license_info(5):
			sync_module.sync_licenses()
		with _mock_license_info(8):
			sync_module.sync_licenses()
		with _mock_license_info(8):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-1")
		outcomes = [row.outcome for row in doc.sync_history]
		self.assertEqual(outcomes, ["Created", "Updated", "Unchanged"])
