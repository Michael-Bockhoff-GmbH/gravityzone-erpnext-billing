# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest

from gravityzone_billing.gravityzone_client import Company, GravityZoneError
from gravityzone_billing.sync import _collect_company_tree


class FakeClient:
	"""children: {parent_id: [Company, ...]}; None is the top level. A parent
	missing from the mapping behaves like a customer-type company, which
	GravityZone answers with error -32602 for parentId.
	"""

	def __init__(self, children, other_error_for=None):
		self.children = children
		self.other_error_for = other_error_for
		self.calls = []

	def get_companies_list(self, parent_id=None):
		self.calls.append(parent_id)
		if self.other_error_for is not None and parent_id == self.other_error_for:
			raise GravityZoneError(-32000, "Server error")
		if parent_id not in self.children:
			raise GravityZoneError(-32602, "Invalid params: Invalid value for 'parentId' parameter.")
		return self.children[parent_id]


def _c(company_id):
	return Company(id=company_id, name=company_id.upper(), raw={})


class TestCompanyTree(unittest.TestCase):
	def test_walks_sub_companies_and_records_their_parent(self):
		client = FakeClient(
			{
				None: [_c("partner-a"), _c("customer-b")],
				"partner-a": [_c("child-1"), _c("child-2")],
			}
		)

		tree = _collect_company_tree(client)

		self.assertEqual(
			[(c.id, parent) for c, parent in tree],
			[("partner-a", None), ("customer-b", None), ("child-1", "partner-a"), ("child-2", "partner-a")],
		)

	def test_customer_type_companies_answering_invalid_params_are_leaves_not_errors(self):
		client = FakeClient({None: [_c("customer-b")]})

		tree = _collect_company_tree(client)

		self.assertEqual([c.id for c, _ in tree], ["customer-b"])

	def test_other_errors_are_not_swallowed(self):
		client = FakeClient({None: [_c("customer-b")]}, other_error_for="customer-b")

		with self.assertRaises(GravityZoneError):
			_collect_company_tree(client)

	def test_walks_several_levels_and_survives_a_cycle(self):
		client = FakeClient(
			{
				None: [_c("a")],
				"a": [_c("b")],
				"b": [_c("c"), _c("a")],  # "a" listed again below "b": must not loop forever
				"c": [],
			}
		)

		tree = _collect_company_tree(client)

		self.assertEqual([c.id for c, _ in tree], ["a", "b", "c"])
