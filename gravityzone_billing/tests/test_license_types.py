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
			"Modell: mspSecurePlus\nAbonnement: Monatlich\nLizenzen:\n    • Endpoint Security: 13\n    • MSP Secure Plus: 13",
		)

	def test_mixed_package_company_shows_every_counter_and_additional_products(self):
		info = {"assignedProtectionModel": "mspSecure", "subscriptionType": 3, "additionalProductTypes": [0]}
		usage = {"endpointMonthlyUsage": 2, "aLaCarteMonthlyUsage": 1, "mspSecurePlusMonthlyUsage": 1}

		summary = _license_summary(info, usage)

		self.assertIn("Modell: mspSecure", summary)
		self.assertIn("Zusatzprodukte: Endpoint Security", summary)
		self.assertIn("A La Carte: 1", summary)
		self.assertIn("MSP Secure Plus: 1", summary)

	def test_unknown_subscription_type_and_no_usage_do_not_crash(self):
		summary = _license_summary({"subscriptionType": 99}, {})

		self.assertEqual(summary, "Modell: n/a\nAbonnement: 99")


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
	def test_licenses_are_grouped_under_each_sub_company(self):
		info = {"assignedProtectionModel": "mspSecure", "subscriptionType": 3}
		subs = [
			("Zweite GmbH", {"endpointMonthlyUsage": 1, "aLaCarteMonthlyUsage": 1}),
			("Erste GmbH", {"endpointMonthlyUsage": 1, "mspSecurePlusMonthlyUsage": 1}),
		]

		summary = _license_summary(info, {}, subs)

		self.assertIn("Eigene Lizenzen:\n    keine\n", summary)
		self.assertIn("Unterfirmen (2):", summary)
		self.assertIn("  Erste GmbH\n      • Endpoint Security: 1\n      • MSP Secure Plus: 1", summary)
		self.assertIn("  Zweite GmbH\n      • A La Carte: 1\n      • Endpoint Security: 1", summary)
		# alphabetical, and each company's licenses come right after its name
		self.assertLess(summary.index("Erste GmbH"), summary.index("Zweite GmbH"))

	def test_parent_with_own_licenses(self):
		summary = _license_summary({}, {"endpointMonthlyUsage": 3}, [("Kind GmbH", {})])

		self.assertIn("Eigene Lizenzen:\n    • Endpoint Security: 3", summary)
		self.assertIn("  Kind GmbH\n      keine Lizenzen", summary)

	def test_plain_company_unchanged(self):
		self.assertNotIn("Eigene Lizenzen", _license_summary({}, {"endpointMonthlyUsage": 1}))
