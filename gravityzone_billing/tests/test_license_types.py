# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest

from gravityzone_billing.sync import _counter_label, _license_summary


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
			"Model: mspSecurePlus | Subscription: Monthly | Usage: Endpoint Security 13, MSP Secure Plus 13",
		)

	def test_mixed_package_company_shows_every_counter_and_additional_products(self):
		info = {"assignedProtectionModel": "mspSecure", "subscriptionType": 3, "additionalProductTypes": [0]}
		usage = {"endpointMonthlyUsage": 2, "aLaCarteMonthlyUsage": 1, "mspSecurePlusMonthlyUsage": 1}

		summary = _license_summary(info, usage)

		self.assertIn("Model: mspSecure", summary)
		self.assertIn("Additional products: Endpoint Security", summary)
		self.assertIn("A La Carte 1", summary)
		self.assertIn("MSP Secure Plus 1", summary)

	def test_unknown_subscription_type_and_no_usage_do_not_crash(self):
		summary = _license_summary({"subscriptionType": 99}, {})

		self.assertEqual(summary, "Model: n/a | Subscription: 99")
