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

	def test_get_companies_list_reads_plain_list_without_paging_params(self):
		# Shape observed on a live cloud tenant: a bare list, and page/perPage are rejected.
		response = _mock_response(
			{
				"jsonrpc": "2.0",
				"id": 1,
				"result": [{"id": "1", "name": "Acme"}, {"id": "2", "name": "Widgets Inc"}],
			}
		)
		with patch.object(self.client._session, "post", return_value=response) as post:
			companies = self.client.get_companies_list()

		self.assertEqual([c.id for c in companies], ["1", "2"])
		self.assertEqual([c.name for c in companies], ["Acme", "Widgets Inc"])
		self.assertEqual(post.call_count, 1)
		sent = post.call_args.kwargs["json"]
		self.assertEqual(sent["method"], "getCompaniesList")
		self.assertEqual(sent["params"], {})

	def test_get_companies_list_sends_parentId_only_when_asked_for_children(self):
		response = _mock_response(
			{"jsonrpc": "2.0", "id": 1, "result": [{"id": "c1", "name": "Child"}]}
		)
		with patch.object(self.client._session, "post", return_value=response) as post:
			children = self.client.get_companies_list(parent_id="parent-1")

		self.assertEqual([c.id for c in children], ["c1"])
		self.assertEqual(post.call_args.kwargs["json"]["params"], {"parentId": "parent-1"})

	def test_get_companies_list_tolerates_items_wrapper(self):
		response = _mock_response(
			{"jsonrpc": "2.0", "id": 1, "result": {"items": [{"id": "1", "name": "Acme"}]}}
		)
		with patch.object(self.client._session, "post", return_value=response):
			companies = self.client.get_companies_list()

		self.assertEqual([c.id for c in companies], ["1"])

	def test_get_company_details_calls_the_companies_service(self):
		response = _mock_response({"jsonrpc": "2.0", "id": 1, "result": {"type": 1, "country": "DE"}})
		with patch.object(self.client._session, "post", return_value=response) as post:
			details = self.client.get_company_details("company-1")

		self.assertEqual(details["country"], "DE")
		self.assertTrue(post.call_args.args[0].endswith("/companies/"))
		self.assertEqual(post.call_args.kwargs["json"]["params"], {"companyId": "company-1"})

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

	def test_get_license_info_reads_live_usedSlots_totalSlots(self):
		# Field names observed on a live cloud tenant (totalSlots is None for slot-less licenses).
		response = _mock_response(
			{"jsonrpc": "2.0", "id": 1, "result": {"usedSlots": 12, "totalSlots": None, "subscriptionType": 2}}
		)
		with patch.object(self.client._session, "post", return_value=response):
			info = self.client.get_license_info("company-1")

		self.assertEqual(info.used_licenses, 12)
		self.assertIsNone(info.allocated_licenses)

	def test_get_license_info_raises_instead_of_reading_zero_when_field_missing(self):
		response = _mock_response({"jsonrpc": "2.0", "id": 1, "result": {"somethingElse": 1}})
		with patch.object(self.client._session, "post", return_value=response):
			with self.assertRaises(GravityZoneError) as ctx:
				self.client.get_license_info("company-1")

		self.assertIn("usedSlots", str(ctx.exception))

	def test_per_product_usage_sums_counters_across_product_types(self):
		# Every usages entry carries the full counter set; a later entry's zeros
		# must not overwrite an earlier entry's real counts.
		response = _mock_response(
			{
				"jsonrpc": "2.0",
				"id": 1,
				"result": {
					"usages": [
						{"productType": 0, "endpointMonthlyUsage": 10, "edrMonthlyUsage": 3},
						{"productType": 5, "endpointMonthlyUsage": 0, "edrMonthlyUsage": 0, "phasrMonthlyUsage": 2},
					]
				},
			}
		)
		with patch.object(self.client._session, "post", return_value=response):
			usages = self.client.get_monthly_usage_per_product_type("company-1", "10/2026")

		self.assertEqual(usages, {"endpointMonthlyUsage": 10, "edrMonthlyUsage": 3, "phasrMonthlyUsage": 2})

	def test_http_401_surfaces_gravityzone_error_details(self):
		response = _mock_response(
			{
				"id": 1,
				"jsonrpc": "2.0",
				"error": {
					"code": -32000,
					"message": "Server error",
					"data": {"details": "Invalid API key. Please generate an API key in Control Center."},
				},
			},
			status_code=401,
		)
		with patch.object(self.client._session, "post", return_value=response):
			with self.assertRaises(GravityZoneError) as ctx:
				self.client.get_license_info("company-1")

		self.assertIn("Invalid API key", str(ctx.exception))

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

	def test_get_monthly_usage_per_product_type_merges_usages_list(self):
		response = _mock_response(
			{
				"jsonrpc": "2.0",
				"id": 1,
				"result": {
					"usages": [
						{"productType": 0, "endpointMonthlyUsage": 10, "edrMonthlyUsage": 3},
						{"productType": 5, "phasrMonthlyUsage": 2},
					]
				},
			}
		)
		with patch.object(self.client._session, "post", return_value=response):
			usages = self.client.get_monthly_usage_per_product_type("company-1", "2026-09")

		self.assertEqual(usages["endpointMonthlyUsage"], 10)
		self.assertEqual(usages["edrMonthlyUsage"], 3)
		self.assertEqual(usages["phasrMonthlyUsage"], 2)

	def test_get_monthly_usage_per_product_type_handles_flat_dict(self):
		response = _mock_response(
			{"jsonrpc": "2.0", "id": 1, "result": {"endpointMonthlyUsage": 4}}
		)
		with patch.object(self.client._session, "post", return_value=response):
			usages = self.client.get_monthly_usage_per_product_type("company-1", "2026-09")

		self.assertEqual(usages, {"endpointMonthlyUsage": 4})

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
