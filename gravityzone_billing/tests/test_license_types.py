# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest

from gravityzone_billing.sync import _counter_label, _license_catalogue, _license_data


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


class TestLicenseData(unittest.TestCase):
	def test_monthly_company_with_one_package(self):
		info = {"assignedProtectionModel": "mspSecurePlus", "subscriptionType": 3, "additionalProductTypes": []}
		usage = {"endpointMonthlyUsage": 13, "mspSecurePlusMonthlyUsage": 13, "edrMonthlyUsage": 0}

		self.assertEqual(
			_license_data(info, usage),
			{
				"model": "mspSecurePlus",
				"subscription": 3,
				"additional": [],
				"licenses": {"Endpoint Security": 13, "MSP Secure Plus": 13},
				"sub_companies": None,
			},
		)

	def test_additional_products_are_named(self):
		data = _license_data({"subscriptionType": 3, "additionalProductTypes": [0, 3]}, {})

		self.assertEqual(data["additional"], ["Endpoint Security", "EDR"])

	def test_missing_fields_do_not_crash(self):
		data = _license_data({}, {})

		self.assertEqual((data["model"], data["subscription"], data["licenses"]), (None, None, {}))


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


class TestParentLicenseData(unittest.TestCase):
	def test_licenses_are_kept_per_sub_company_sorted_by_name(self):
		subs = [
			("Zweite GmbH", {"endpointMonthlyUsage": 1, "aLaCarteMonthlyUsage": 1}),
			("Erste GmbH", {"endpointMonthlyUsage": 1, "mspSecurePlusMonthlyUsage": 1, "edrMonthlyUsage": 0}),
		]

		data = _license_data({"assignedProtectionModel": "mspSecure"}, {}, subs)

		self.assertEqual(data["licenses"], {})
		self.assertEqual(
			data["sub_companies"],
			[
				{"name": "Erste GmbH", "licenses": {"Endpoint Security": 1, "MSP Secure Plus": 1}},
				{"name": "Zweite GmbH", "licenses": {"A La Carte": 1, "Endpoint Security": 1}},
			],
		)

	def test_parent_with_own_licenses_and_an_idle_sub_company(self):
		data = _license_data({}, {"endpointMonthlyUsage": 3}, [("Kind GmbH", {})])

		self.assertEqual(data["licenses"], {"Endpoint Security": 3})
		self.assertEqual(data["sub_companies"], [{"name": "Kind GmbH", "licenses": {}}])

	def test_plain_company_has_no_sub_company_block(self):
		self.assertIsNone(_license_data({}, {"endpointMonthlyUsage": 1})["sub_companies"])
