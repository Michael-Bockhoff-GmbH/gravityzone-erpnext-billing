# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from gravityzone_billing.sync import _check_settings


def _settings(**overrides):
	values = {
		"billing_backend": "ERPNext Subscription",
		"license_metric": "Monthly Usage",
		"default_company": "Acme GmbH",
		"subscription_plan": "GZ Seat",
		"item": "",
	}
	values.update(overrides)
	return SimpleNamespace(**values)


class TestSyncPreflight(unittest.TestCase):
	"""The sync must name what's missing instead of letting every company fail
	with ERPNext's cryptic "Value missing for Subscription Plan".
	"""

	def test_complete_flat_settings_pass(self):
		_check_settings(_settings())

	def test_missing_plan_and_company_are_both_named(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			_check_settings(_settings(subscription_plan="", default_company=""))

		message = str(ctx.exception)
		self.assertIn("Default Company is not set", message)
		self.assertIn("Subscription Plan is not set", message)

	def test_simple_subscription_backend_needs_an_item_not_a_plan(self):
		settings = _settings(billing_backend="ALYF Simple Subscription", subscription_plan="GZ Seat", item="")
		with self.assertRaises(frappe.ValidationError) as ctx:
			_check_settings(settings)

		self.assertIn("Item is not set", str(ctx.exception))

	def _check_per_product(self, mappings, **settings_overrides):
		"""Run _check_settings in per-product mode with the given enabled mappings.

		Only the `frappe` name inside gravityzone_billing.sync is replaced (never
		`frappe.get_all` itself, which Frappe's own internals also call).
		"""

		def throw(message):
			raise frappe.ValidationError(message)

		settings = _settings(license_metric="Per-Product Monthly Usage", subscription_plan="", **settings_overrides)
		with patch("gravityzone_billing.sync.frappe") as fake_frappe:
			fake_frappe.get_all.return_value = [SimpleNamespace(**m) for m in mappings]
			fake_frappe.throw.side_effect = throw
			_check_settings(settings)

	def test_per_product_mode_needs_an_enabled_mapping(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._check_per_product([])

		self.assertIn("no enabled GravityZone Product Mapping", str(ctx.exception))

	def test_per_product_mode_rejects_an_enabled_mapping_without_a_plan(self):
		mappings = [{"name": "edrMonthlyUsage", "subscription_plan": "", "item": ""}]
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._check_per_product(mappings)

		message = str(ctx.exception)
		self.assertIn("Subscription Plan", message)
		self.assertIn("edrMonthlyUsage", message)

	def test_per_product_mode_uses_the_item_field_for_the_simple_backend(self):
		mappings = [{"name": "edrMonthlyUsage", "subscription_plan": "Some Plan", "item": ""}]
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._check_per_product(mappings, billing_backend="ALYF Simple Subscription")

		self.assertIn("Item", str(ctx.exception))

	def test_company_without_customer_is_skipped_not_synced(self):
		from gravityzone_billing.sync import sync_one_company

		company = SimpleNamespace(customer=None, exclude_from_sync=0)
		# Replace the whole `frappe` name inside gravityzone_billing.sync only. Patching
		# `frappe.get_doc` itself would also hijack Frappe's own internal calls (e.g. the
		# System Settings lookup) and can leave a fake object in the shared Redis cache.
		with (
			patch("gravityzone_billing.sync.frappe") as fake_frappe,
			patch("gravityzone_billing.sync.now_datetime", return_value="2026-10-07 12:00:00"),
		):
			fake_frappe.get_doc.return_value = company
			# client=None: a skipped company must never reach the GravityZone API
			outcome = sync_one_company(None, _settings(), "gz-1")

		self.assertEqual(outcome, "skipped")
		saved = fake_frappe.db.set_value.call_args.args[2]
		self.assertIn("no ERPNext Customer", saved["last_sync_message"])

	def test_excluded_company_is_skipped_even_with_a_customer(self):
		from gravityzone_billing.sync import sync_one_company

		company = SimpleNamespace(customer="Example Customer", exclude_from_sync=1)
		with (
			patch("gravityzone_billing.sync.frappe") as fake_frappe,
			patch("gravityzone_billing.sync.now_datetime", return_value="2026-10-07 12:00:00"),
		):
			fake_frappe.get_doc.return_value = company
			outcome = sync_one_company(None, _settings(), "gz-parent")

		self.assertEqual(outcome, "skipped")
		saved = fake_frappe.db.set_value.call_args.args[2]
		self.assertIn("excluded from sync", saved["last_sync_message"])

	def test_create_missing_customers_targets_only_unassigned_non_excluded_companies(self):
		from gravityzone_billing.sync import create_missing_erpnext_customers

		with (
			patch("gravityzone_billing.sync.frappe") as fake_frappe,
			patch(
				"gravityzone_billing.sync.create_erpnext_customers",
				return_value={"created": 2, "linked": 0, "skipped": 0},
			) as create,
		):
			fake_frappe.get_all.return_value = ["gz-a", "gz-b"]
			result = create_missing_erpnext_customers()

		filters = fake_frappe.get_all.call_args.kwargs["filters"]
		self.assertEqual(filters, {"customer": ["is", "not set"], "exclude_from_sync": 0})
		self.assertEqual(create.call_args.args[0], '["gz-a", "gz-b"]')
		self.assertEqual(result["created"], 2)

	def test_per_product_mode_with_an_assigned_mapping_passes_without_a_flat_plan(self):
		mappings = [{"name": "edrMonthlyUsage", "subscription_plan": "EDR Plan", "item": ""}]
		self._check_per_product(mappings)
