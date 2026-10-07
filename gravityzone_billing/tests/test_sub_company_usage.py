# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest
from types import SimpleNamespace

from gravityzone_billing.gravityzone_client import Company, GravityZoneError, LicenseInfo
from gravityzone_billing.sync import _get_license_qty, _subtract_usage


class TestSubtractUsage(unittest.TestCase):
	def test_parent_with_no_own_seats_comes_out_at_zero(self):
		total = {"endpointMonthlyUsage": 2, "mspSecurePlusMonthlyUsage": 1, "aLaCarteMonthlyUsage": 1}
		children = [
			{"endpointMonthlyUsage": 1, "mspSecurePlusMonthlyUsage": 1, "aLaCarteMonthlyUsage": 0},
			{"endpointMonthlyUsage": 1, "mspSecurePlusMonthlyUsage": 0, "aLaCarteMonthlyUsage": 1},
		]

		self.assertEqual(
			_subtract_usage(total, children),
			{"endpointMonthlyUsage": 0, "mspSecurePlusMonthlyUsage": 0, "aLaCarteMonthlyUsage": 0},
		)

	def test_parent_with_its_own_seats_keeps_exactly_those(self):
		total = {"endpointMonthlyUsage": 7, "edrMonthlyUsage": 3}
		children = [{"endpointMonthlyUsage": 2, "edrMonthlyUsage": 1}, {"endpointMonthlyUsage": 1}]

		self.assertEqual(_subtract_usage(total, children), {"endpointMonthlyUsage": 4, "edrMonthlyUsage": 2})

	def test_never_goes_below_zero(self):
		self.assertEqual(_subtract_usage({"endpointMonthlyUsage": 1}, [{"endpointMonthlyUsage": 5}]), {"endpointMonthlyUsage": 0})

	def test_no_children_changes_nothing(self):
		self.assertEqual(_subtract_usage({"endpointMonthlyUsage": 4}, []), {"endpointMonthlyUsage": 4})


class FakeClient:
	"""license_info: {company_id: usedSlots}; children: {parent_id: [Company, ...]}"""

	def __init__(self, license_info, children):
		self.license_info = license_info
		self.children = children

	def get_license_info(self, company_id):
		return LicenseInfo(company_id, self.license_info[company_id], None, {})

	def get_companies_list(self, parent_id=None):
		if parent_id not in self.children:
			raise GravityZoneError(-32602, "Invalid params: parentId")
		return self.children[parent_id]


def _company(**overrides):
	values = {"gz_company_id": "parent", "usage_includes_sub_companies": 0, "min_qty": 0}
	values.update(overrides)
	return SimpleNamespace(**values)


SETTINGS = SimpleNamespace(license_metric="License Info")


class TestFlatQuantityForParents(unittest.TestCase):
	def setUp(self):
		self.client = FakeClient(
			{"parent": 5, "kid-1": 1, "kid-2": 1},
			{"parent": [Company("kid-1", "Kid 1", {}), Company("kid-2", "Kid 2", {})]},
		)

	def test_unmarked_company_is_billed_for_everything_it_reports(self):
		self.assertEqual(_get_license_qty(self.client, SETTINGS, _company()), 5)

	def test_marked_parent_is_billed_only_for_its_own_share(self):
		self.assertEqual(_get_license_qty(self.client, SETTINGS, _company(usage_includes_sub_companies=1)), 3)

	def test_marked_company_without_children_is_unaffected(self):
		client = FakeClient({"leaf": 4}, {})

		self.assertEqual(_get_license_qty(client, SETTINGS, _company(gz_company_id="leaf", usage_includes_sub_companies=1)), 4)

	def test_minimum_floor_still_applies_after_subtraction(self):
		company = _company(usage_includes_sub_companies=1, min_qty=10)

		self.assertEqual(_get_license_qty(self.client, SETTINGS, company), 10)
