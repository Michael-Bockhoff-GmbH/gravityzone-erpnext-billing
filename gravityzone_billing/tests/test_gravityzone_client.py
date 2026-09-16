# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest
from unittest.mock import MagicMock, patch

from gravityzone_billing.gravityzone_client import GravityZoneClient, GravityZoneError

BASE_URL = "https://cloud.gravityzone.bitdefender.com/api/v1.0/jsonrpc"


def _mock_response(json_body):
	response = MagicMock()
	response.json.return_value = json_body
	response.raise_for_status.return_value = None
	return response


class TestGravityZoneClient(unittest.TestCase):
	def setUp(self):
		self.client = GravityZoneClient(api_key="test-key", base_url=BASE_URL)

	def test_get_companies_list_paginates(self):
		page1 = _mock_response(
			{
				"jsonrpc": "2.0",
				"id": 1,
				"result": {"items": [{"id": "1", "name": "Acme"}], "page": 1, "pagesCount": 2},
			}
		)
		page2 = _mock_response(
			{
				"jsonrpc": "2.0",
				"id": 2,
				"result": {"items": [{"id": "2", "name": "Widgets Inc"}], "page": 2, "pagesCount": 2},
			}
		)
		with patch.object(self.client._session, "post", side_effect=[page1, page2]) as post:
			companies = self.client.get_companies_list()

		self.assertEqual([c.id for c in companies], ["1", "2"])
		self.assertEqual([c.name for c in companies], ["Acme", "Widgets Inc"])
		self.assertEqual(post.call_count, 2)

	def test_get_license_info(self):
		response = _mock_response(
			{"jsonrpc": "2.0", "id": 1, "result": {"usedLicenses": 7, "additionalLicenses": 10}}
		)
		with patch.object(self.client._session, "post", return_value=response) as post:
			info = self.client.get_license_info("company-1")

		self.assertEqual(info.used_licenses, 7)
		self.assertEqual(info.allocated_licenses, 10)
		kwargs = post.call_args.kwargs
		self.assertEqual(kwargs["auth"], ("test-key", ""))
		self.assertEqual(kwargs["json"]["method"], "getLicenseInfo")

	def test_raises_on_jsonrpc_error(self):
		response = _mock_response(
			{"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Invalid request"}}
		)
		with patch.object(self.client._session, "post", return_value=response):
			with self.assertRaises(GravityZoneError):
				self.client.get_license_info("company-1")
