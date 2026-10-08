# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest

from gravityzone_billing.sync import _without_overlapping_endpoint


class TestEndpointExclusion(unittest.TestCase):
	def test_msp_secure_plus_excludes_endpoint(self):
		usages = {"endpointMonthlyUsage": 13, "mspSecurePlusMonthlyUsage": 13}

		self.assertEqual(_without_overlapping_endpoint(usages)["endpointMonthlyUsage"], 0)
		self.assertEqual(_without_overlapping_endpoint(usages)["mspSecurePlusMonthlyUsage"], 13)

	def test_every_package_variant_excludes_endpoint(self):
		for field in ("mspSecureMonthlyUsage", "mspSecurePlusMonthlyUsage", "mspSecureExtraMonthlyUsage"):
			with self.subTest(field=field):
				result = _without_overlapping_endpoint({"endpointMonthlyUsage": 5, field: 1})
				self.assertEqual(result["endpointMonthlyUsage"], 0)

	def test_no_package_keeps_endpoint(self):
		usages = {"endpointMonthlyUsage": 4, "mspSecurePlusMonthlyUsage": 0, "aLaCarteMonthlyUsage": 4}

		self.assertEqual(_without_overlapping_endpoint(usages), usages)

	def test_input_is_not_mutated(self):
		usages = {"endpointMonthlyUsage": 3, "mspSecureMonthlyUsage": 3}
		_without_overlapping_endpoint(usages)

		self.assertEqual(usages["endpointMonthlyUsage"], 3)

	def test_other_counters_do_not_count_as_package(self):
		usages = {"endpointMonthlyUsage": 2, "edrMonthlyUsage": 2}

		self.assertEqual(_without_overlapping_endpoint(usages)["endpointMonthlyUsage"], 2)
