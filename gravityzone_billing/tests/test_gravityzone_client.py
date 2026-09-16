# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest
from unittest.mock import MagicMock, patch

import requests

from gravityzone_billing.gravityzone_client import GravityZoneClient, GravityZoneError

BASE_URL = "https://cloud.gravityzone.bitdefender.com/api/v1.0/jsonrpc"


def _mock_response(json_body, status_code=200, headers=None):
	response = MagicMock()
	response.json.return_value = json_body
	response.status_code = status_code
	response.headers = headers or {}
	if status_code >= 400:
		response.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error")
	else:
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

	def test_retries_on_429_then_succeeds(self):
		throttled = _mock_response({}, status_code=429, headers={"Retry-After": "0"})
		ok = _mock_response({"jsonrpc": "2.0", "id": 1, "result": {"usedLicenses": 3}})
		with (
			patch.object(self.client._session, "post", side_effect=[throttled, ok]) as post,
			patch("gravityzone_billing.gravityzone_client.time.sleep") as sleep,
		):
			info = self.client.get_license_info("company-1")

		self.assertEqual(info.used_licenses, 3)
		self.assertEqual(post.call_count, 2)
		sleep.assert_called()

	def test_gives_up_after_max_retries_on_429(self):
		throttled = _mock_response({}, status_code=429)
		with (
			patch.object(self.client._session, "post", return_value=throttled) as post,
			patch("gravityzone_billing.gravityzone_client.time.sleep"),
		):
			with self.assertRaises(Exception):
				self.client.get_license_info("company-1")

		from gravityzone_billing.gravityzone_client import MAX_RETRIES

		self.assertEqual(post.call_count, MAX_RETRIES)

	def test_throttles_between_calls(self):
		response = _mock_response({"jsonrpc": "2.0", "id": 1, "result": {"usedLicenses": 1}})
		with (
			patch.object(self.client._session, "post", return_value=response),
			patch("gravityzone_billing.gravityzone_client.time.sleep") as sleep,
		):
			self.client.get_license_info("company-1")
			self.client.get_license_info("company-1")

		# second call should have measured the elapsed gap and (in the mocked,
		# effectively-zero-elapsed-time case) asked to wait roughly one interval
		sleep.assert_called()
