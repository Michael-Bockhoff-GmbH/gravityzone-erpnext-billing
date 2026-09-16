# Copyright (c) 2026, Michael Bockhoff GmbH and Contributors
# See license.txt

import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from gravityzone_billing import sync as sync_module
from gravityzone_billing.gravityzone_client import LicenseInfo
from gravityzone_billing.sync import approve_all_pending, approve_pending_change

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


def _mock_usage_per_product(**usages):
	return patch.object(
		sync_module.GravityZoneClient,
		"get_monthly_usage_per_product_type",
		return_value=usages,
	)


class IntegrationTestGravityZonePerProduct(IntegrationTestCase):
	"""Per-Product Monthly Usage mode: each GravityZone product bills as its
	own line via GravityZone Product Mapping, tracked and reviewed
	independently on the company's Product Lines table.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.customer = frappe.db.get_value("Customer", {}, "name") or cls._make_customer()
		cls.company = frappe.db.get_value("Company", {}, "name")

		for item_code, plan_name in [
			("GZ-TEST-EP", "GZ Test Endpoint Plan"),
			("GZ-TEST-EDR", "GZ Test EDR Plan"),
		]:
			if not frappe.db.exists("Item", item_code):
				frappe.get_doc(
					{
						"doctype": "Item",
						"item_code": item_code,
						"item_name": item_code,
						"item_group": frappe.db.get_value("Item Group", {}, "name") or "All Item Groups",
						"stock_uom": "Nos",
						"is_stock_item": 0,
					}
				).insert(ignore_permissions=True)
			if not frappe.db.exists("Subscription Plan", plan_name):
				frappe.get_doc(
					{
						"doctype": "Subscription Plan",
						"plan_name": plan_name,
						"item": item_code,
						"price_determination": "Fixed Rate",
						"cost": 1.0,
						"currency": frappe.db.get_default("currency") or "USD",
						"billing_interval": "Month",
						"billing_interval_count": 1,
					}
				).insert(ignore_permissions=True)

		for gz_field, label, plan_name in [
			("endpointMonthlyUsage", "Endpoint Security", "GZ Test Endpoint Plan"),
			("edrMonthlyUsage", "EDR", "GZ Test EDR Plan"),
		]:
			if not frappe.db.exists("GravityZone Product Mapping", gz_field):
				frappe.get_doc(
					{
						"doctype": "GravityZone Product Mapping",
						"gz_usage_field": gz_field,
						"label": label,
						"subscription_plan": plan_name,
						"enabled": 1,
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
		settings.license_metric = "Per-Product Monthly Usage"
		settings.default_company = self.company
		settings.sync_enabled = 1
		settings.review_threshold_percent = 50
		settings.review_threshold_min_seats = 5
		settings.save(ignore_permissions=True)

		if frappe.db.exists("GravityZone Company", "gz-co-2"):
			frappe.delete_doc("GravityZone Company", "gz-co-2", force=True, ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "GravityZone Company",
				"gz_company_id": "gz-co-2",
				"gz_company_name": "Test Co 2",
				"customer": self.customer,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		doc = frappe.get_doc("GravityZone Company", "gz-co-2")
		if doc.subscription and frappe.db.exists("Subscription", doc.subscription):
			frappe.delete_doc("Subscription", doc.subscription, force=True, ignore_permissions=True)
		frappe.delete_doc("GravityZone Company", "gz-co-2", force=True, ignore_permissions=True)

	def test_first_sync_creates_one_subscription_with_all_product_lines(self):
		with _mock_usage_per_product(endpointMonthlyUsage=10, edrMonthlyUsage=3):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-2")
		self.assertFalse(doc.needs_review)
		lines = {row.label: row.last_synced_qty for row in doc.product_lines}
		self.assertEqual(lines, {"Endpoint Security": 10, "EDR": 3})

		subscription = frappe.get_doc("Subscription", doc.subscription)
		plans = {p.plan: p.qty for p in subscription.plans}
		self.assertEqual(plans, {"GZ Test Endpoint Plan": 10, "GZ Test EDR Plan": 3})

	def test_large_jump_flags_only_that_product_line(self):
		with _mock_usage_per_product(endpointMonthlyUsage=10, edrMonthlyUsage=3):
			sync_module.sync_licenses()
		with _mock_usage_per_product(endpointMonthlyUsage=10, edrMonthlyUsage=20):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-2")
		self.assertTrue(doc.needs_review)
		lines = {row.label: row for row in doc.product_lines}
		self.assertTrue(lines["EDR"].needs_review)
		self.assertEqual(lines["EDR"].pending_qty, 20)
		self.assertFalse(lines["Endpoint Security"].needs_review)

		subscription = frappe.get_doc("Subscription", doc.subscription)
		plans = {p.plan: p.qty for p in subscription.plans}
		self.assertEqual(plans, {"GZ Test Endpoint Plan": 10, "GZ Test EDR Plan": 3})

	def test_approve_all_pending_applies_only_flagged_lines(self):
		with _mock_usage_per_product(endpointMonthlyUsage=10, edrMonthlyUsage=3):
			sync_module.sync_licenses()
		with _mock_usage_per_product(endpointMonthlyUsage=10, edrMonthlyUsage=20):
			sync_module.sync_licenses()

		result = approve_all_pending(company_name="gz-co-2")
		self.assertEqual(result["approved"], 1)

		doc = frappe.get_doc("GravityZone Company", "gz-co-2")
		self.assertFalse(doc.needs_review)
		lines = {row.label: row.last_synced_qty for row in doc.product_lines}
		self.assertEqual(lines, {"Endpoint Security": 10, "EDR": 20})

		subscription = frappe.get_doc("Subscription", doc.subscription)
		plans = {p.plan: p.qty for p in subscription.plans}
		self.assertEqual(plans, {"GZ Test Endpoint Plan": 10, "GZ Test EDR Plan": 20})


class IntegrationTestSimpleSubscriptionBackend(IntegrationTestCase):
	"""ALYF Simple Subscription (github.com/alyf-de/simple_subscription) as
	the billing backend instead of core ERPNext Subscription: create+submit,
	update via a direct child-row write (Simple Subscription doesn't allow
	normal edits to a submitted document's items table), and the review
	guard/approval flow. Skipped if that optional app isn't installed.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("DocType", "Simple Subscription"):
			raise unittest.SkipTest("simple_subscription app not installed")

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
		settings.billing_backend = "ALYF Simple Subscription"
		settings.item = "GZ-TEST-SEAT"
		settings.default_company = self.company
		settings.sync_enabled = 1
		settings.review_threshold_percent = 50
		settings.review_threshold_min_seats = 5
		settings.save(ignore_permissions=True)

		if frappe.db.exists("GravityZone Company", "gz-co-3"):
			frappe.delete_doc("GravityZone Company", "gz-co-3", force=True, ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "GravityZone Company",
				"gz_company_id": "gz-co-3",
				"gz_company_name": "Test Co 3",
				"customer": self.customer,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		doc = frappe.get_doc("GravityZone Company", "gz-co-3")
		if doc.subscription and doc.billing_doctype and frappe.db.exists(doc.billing_doctype, doc.subscription):
			billing_doc = frappe.get_doc(doc.billing_doctype, doc.subscription)
			if billing_doc.docstatus == 1:
				billing_doc.cancel()
			frappe.delete_doc(doc.billing_doctype, doc.subscription, force=True, ignore_permissions=True)
		frappe.delete_doc("GravityZone Company", "gz-co-3", force=True, ignore_permissions=True)

		frappe.db.set_single_value("GravityZone Settings", "billing_backend", "ERPNext Subscription")

	def test_first_sync_creates_and_submits_a_simple_subscription(self):
		with _mock_license_info(5):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-3")
		self.assertEqual(doc.billing_doctype, "Simple Subscription")
		self.assertEqual(doc.last_synced_qty, 5)

		simple_sub = frappe.get_doc("Simple Subscription", doc.subscription)
		self.assertEqual(simple_sub.docstatus, 1)
		self.assertEqual(simple_sub.items[0].item, "GZ-TEST-SEAT")
		self.assertEqual(simple_sub.items[0].qty, 5)

	def test_updates_qty_on_the_submitted_document(self):
		with _mock_license_info(5):
			sync_module.sync_licenses()
		with _mock_license_info(8):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-3")
		self.assertEqual(doc.last_synced_qty, 8)

		simple_sub = frappe.get_doc("Simple Subscription", doc.subscription)
		self.assertEqual(simple_sub.docstatus, 1)
		self.assertEqual(simple_sub.items[0].qty, 8)

	def test_large_jump_flagged_and_document_left_untouched(self):
		with _mock_license_info(8):
			sync_module.sync_licenses()
		with _mock_license_info(60):
			sync_module.sync_licenses()

		doc = frappe.get_doc("GravityZone Company", "gz-co-3")
		self.assertTrue(doc.needs_review)
		self.assertEqual(doc.pending_qty, 60)
		self.assertEqual(doc.last_synced_qty, 8)

		simple_sub = frappe.get_doc("Simple Subscription", doc.subscription)
		self.assertEqual(simple_sub.items[0].qty, 8)

	def test_approve_all_pending_applies_it(self):
		with _mock_license_info(8):
			sync_module.sync_licenses()
		with _mock_license_info(60):
			sync_module.sync_licenses()

		approve_all_pending(company_name="gz-co-3")

		doc = frappe.get_doc("GravityZone Company", "gz-co-3")
		self.assertFalse(doc.needs_review)
		self.assertEqual(doc.last_synced_qty, 60)

		simple_sub = frappe.get_doc("Simple Subscription", doc.subscription)
		self.assertEqual(simple_sub.items[0].qty, 60)
