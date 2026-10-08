# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest

from gravityzone_billing.sync import _counter_label, _license_catalogue, _license_summary


class TestCounterLabel(unittest.TestCase):
	def test_endpoint_counter_gets_its_product_name(self):
		self.assertEqual(_counter_label("endpointMonthlyUsage"), "Endpoint Security")

	def test_msp_package_counters(self):
		self.assertEqual(_counter_label("mspSecurePlusMonthlyUsage"), "MSP Secure Plus")
		self.assertEqual(_counter_label("mspSecureEssentialsMonthlyUsage"), "MSP Secure Essentials")

	def test_a_la_carte(self):
		self.assertEqual(_counter_label("aLaCarteMonthlyUsage"), "A La Carte")

	def test_acronyms_stay_uppercase(self):
		self.assertEqual(_counter_label("edrMonthlyUsage"), "EDR")
		self.assertEqual(_counter_label("atsMonthlyUsage"), "ATS")

	def test_counter_without_monthly_suffix(self):
		self.assertEqual(_counter_label("integrityMonitoringUsage"), "Integrity Monitoring")


class TestLicenseSummary(unittest.TestCase):
	def test_monthly_company_with_one_package(self):
		info = {"assignedProtectionModel": "mspSecurePlus", "subscriptionType": 3, "additionalProductTypes": []}
		usage = {"endpointMonthlyUsage": 13, "mspSecurePlusMonthlyUsage": 13}

		self.assertEqual(
			_license_summary(info, usage),
			"Model: mspSecurePlus\nSubscription: Monthly\nUsage:\n    • Endpoint Security: 13\n    • MSP Secure Plus: 13",
		)

	def test_mixed_package_company_shows_every_counter_and_additional_products(self):
		info = {"assignedProtectionModel": "mspSecure", "subscriptionType": 3, "additionalProductTypes": [0]}
		usage = {"endpointMonthlyUsage": 2, "aLaCarteMonthlyUsage": 1, "mspSecurePlusMonthlyUsage": 1}

		summary = _license_summary(info, usage)

		self.assertIn("Model: mspSecure", summary)
		self.assertIn("Additional products: Endpoint Security", summary)
		self.assertIn("A La Carte: 1", summary)
		self.assertIn("MSP Secure Plus: 1", summary)

	def test_unknown_subscription_type_and_no_usage_do_not_crash(self):
		summary = _license_summary({"subscriptionType": 99}, {})

		self.assertEqual(summary, "Model: n/a\nSubscription: 99")


class TestLicenseCatalogue(unittest.TestCase):
	def test_lists_every_counter_even_when_nobody_uses_it(self):
		usages = [
			{"endpointMonthlyUsage": 13, "mspSecurePlusMonthlyUsage": 13, "edrMonthlyUsage": 0},
			{"endpointMonthlyUsage": 1, "mspSecurePlusMonthlyUsage": 0, "edrMonthlyUsage": 0},
		]

		self.assertEqual(
			_license_catalogue(usages),
			{"endpointMonthlyUsage": 2, "mspSecurePlusMonthlyUsage": 1, "edrMonthlyUsage": 0},
		)

	def test_counter_missing_for_one_company_still_appears(self):
		usages = [{"endpointMonthlyUsage": 2}, {"endpointMonthlyUsage": 1, "aLaCarteMonthlyUsage": 1}]

		self.assertEqual(_license_catalogue(usages), {"endpointMonthlyUsage": 2, "aLaCarteMonthlyUsage": 1})

	def test_no_companies_no_catalogue(self):
		self.assertEqual(_license_catalogue([]), {})


class TestParentLicenseSummary(unittest.TestCase):
	def test_parent_shows_own_usage_and_sub_companies_separately(self):
		info = {"assignedProtectionModel": "mspSecure", "subscriptionType": 3}

		summary = _license_summary(
			info, {}, sub_company_total={"endpointMonthlyUsage": 2, "mspSecurePlusMonthlyUsage": 1}, sub_company_count=2
		)

		self.assertIn("Own usage:\n    none", summary)
		self.assertIn("Sub-companies (2), combined:\n    • Endpoint Security: 2\n    • MSP Secure Plus: 1", summary)

	def test_parent_with_own_licenses(self):
		summary = _license_summary({}, {"endpointMonthlyUsage": 3}, sub_company_total={}, sub_company_count=1)

		self.assertIn("Own usage:\n    • Endpoint Security: 3", summary)
		self.assertIn("Sub-companies (1), combined:\n    no usage", summary)

	def test_plain_company_unchanged(self):
		self.assertNotIn("Own usage", _license_summary({}, {"endpointMonthlyUsage": 1}))
