"""Thin client for the Bitdefender GravityZone MSP/Partner JSON-RPC API.

GravityZone exposes one JSON-RPC 2.0 endpoint per "service" (module), e.g.
``.../jsonrpc/network``, ``.../jsonrpc/licensing``. Auth is HTTP Basic with
the API key as the username and an empty password.

NOTE: Bitdefender's public docs for the Partner API live behind a JS-rendered
partner portal, so the exact request/response field names below were compiled
from Bitdefender's published API guides and third-party integrations rather
than a live call. Before relying on this in production, generate an API key
in Control Center and diff a real response against the shapes assumed here
(see README "Verifying against the real API").
"""

import itertools
import time

import requests

DEFAULT_TIMEOUT = 30
MIN_CALL_INTERVAL = 0.25  # 4 req/sec: under the 5 req/sec per-key limit Bitdefender documents for the Licensing methods
MAX_RETRIES = 5
INITIAL_BACKOFF = 1.0


class GravityZoneError(RuntimeError):
	"""Raised when the GravityZone API returns a JSON-RPC error object."""

	def __init__(self, code, message, data=None):
		super().__init__(f"GravityZone API error {code}: {message}")
		self.code = code
		self.message = message
		self.data = data


class Company:
	def __init__(self, id, name, raw):
		self.id = id
		self.name = name
		self.raw = raw


class LicenseInfo:
	def __init__(self, company_id, used_licenses, allocated_licenses, raw):
		self.company_id = company_id
		self.used_licenses = used_licenses
		self.allocated_licenses = allocated_licenses
		self.raw = raw


class GravityZoneClient:
	def __init__(self, api_key, base_url, session=None, timeout=DEFAULT_TIMEOUT):
		self._api_key = api_key
		self._base_url = base_url.rstrip("/")
		self._session = session or requests.Session()
		self._timeout = timeout
		self._id_counter = itertools.count(1)
		self._last_call_at = 0.0

	def _throttle(self):
		"""Keep our own call rate under GravityZone's documented per-key cap."""
		wait = MIN_CALL_INTERVAL - (time.monotonic() - self._last_call_at)
		if wait > 0:
			time.sleep(wait)

	def _call(self, service, method, params=None):
		url = f"{self._base_url}/{service}/"
		payload = {
			"jsonrpc": "2.0",
			"id": next(self._id_counter),
			"method": method,
			"params": params or {},
		}

		backoff = INITIAL_BACKOFF
		for attempt in range(1, MAX_RETRIES + 1):
			self._throttle()
			response = self._session.post(
				url,
				json=payload,
				auth=(self._api_key, ""),
				headers={"Content-Type": "application/json"},
				timeout=self._timeout,
			)
			self._last_call_at = time.monotonic()

			if response.status_code == 429 and attempt < MAX_RETRIES:
				retry_after = response.headers.get("Retry-After")
				time.sleep(float(retry_after) if retry_after else backoff)
				backoff *= 2
				continue

			# GravityZone explains failures in a JSON-RPC error body even on HTTP 4xx
			# (e.g. 401 "Invalid API key"), so surface that before raise_for_status
			# replaces it with a bare status line.
			try:
				body = response.json()
			except ValueError:
				body = None

			if isinstance(body, dict) and body.get("error"):
				error = body["error"]
				message = error.get("message", "unknown error")
				details = (error.get("data") or {}).get("details")
				raise GravityZoneError(
					code=error.get("code"),
					message=f"{message}: {details}" if details else message,
					data=error.get("data"),
				)

			response.raise_for_status()
			return (body or {}).get("result")

	def get_companies_list(self, parent_id=None):
		"""List the companies directly under ``parent_id`` (default: the API key's own
		company) — one level only, there is no recursive option.

		Maps to the Network service ``getCompaniesList`` method. Verified against
		a live cloud tenant: ``page``/``perPage`` are rejected with "Invalid
		params"; the result is a plain, unpaginated list of ``{"id", "name"}``
		objects. Passing the id of a partner-type company (``type`` 0) returns its
		sub-companies; for a customer-type company (``type`` 1) GravityZone
		answers error -32602 "Invalid value for 'parentId'", which callers walking
		the tree treat as "no children". A ``{"items": [...]}`` wrapper is still
		tolerated in case other GravityZone versions differ.
		"""
		params = {"parentId": parent_id} if parent_id else {}
		result = self._call("network", "getCompaniesList", params)
		items = result.get("items", []) if isinstance(result, dict) else (result or [])
		return [Company(id=str(item.get("id")), name=item.get("name", ""), raw=item) for item in items]

	def get_license_info(self, company_id):
		"""Current seat usage for a company (Licensing service).

		Verified against a live cloud tenant: the seat counts are ``usedSlots``
		and ``totalSlots`` (None when the license has no fixed slot count). The
		older ``usedLicenses``/``additionalLicenses`` names are still accepted.
		A response with none of the used-count fields raises instead of
		reading as 0, which would silently bill everyone for nothing.
		"""
		result = self._call("licensing", "getLicenseInfo", {"companyId": company_id}) or {}
		used = next((result[k] for k in ("usedSlots", "usedLicenses", "used") if result.get(k) is not None), None)
		if used is None:
			raise GravityZoneError(
				None,
				f"getLicenseInfo returned no used-seat field (expected usedSlots); got: {sorted(result)}",
			)
		allocated = next(
			(result[k] for k in ("totalSlots", "additionalLicenses", "allocatedLicenses") if result.get(k) is not None),
			None,
		)
		return LicenseInfo(
			company_id=company_id,
			used_licenses=int(used),
			allocated_licenses=int(allocated) if allocated is not None else None,
			raw=result,
		)

	def get_monthly_usage(self, company_id, target_month):
		"""Actual monthly consumption for a company.

		``target_month`` must be ``mm/yyyy`` (e.g. ``"10/2026"``) — GravityZone
		rejects any other format with "Invalid params". Returns the flat dict of
		counters (``endpointMonthlyUsage``, ``edrMonthlyUsage``, ...). This is
		Bitdefender's recommended source of truth for MSP billing
		reconciliation, as opposed to the point-in-time ``getLicenseInfo``.
		"""
		return self._call("licensing", "getMonthlyUsage", {"companyId": company_id, "targetMonth": target_month})

	def get_monthly_usage_per_product_type(self, company_id, target_month):
		"""Monthly usage broken down by GravityZone product (Endpoint Security's
		own add-on modules — EDR, Patch Management, Full Disk Encryption, Email
		Security, etc. — plus any other product types the company holds), unlike
		``get_monthly_usage`` which only covers the default Endpoint Security
		product.

		Same ``mm/yyyy`` ``target_month`` format as ``get_monthly_usage``.

		Verified against a live cloud tenant: the response is
		``{"usages": [{...counters..., "productType": N}, ...]}`` with one entry
		per product type, and *every* entry carries the full set of counters.
		Returns a flat ``{usage_field: count}`` dict with each counter summed
		across entries (merging with ``dict.update`` would let a later entry's
		zeros overwrite an earlier entry's real counts). A top-level flat dict
		of counters is passed through unchanged.
		"""
		result = (
			self._call(
				"licensing",
				"getMonthlyUsagePerProductType",
				{"companyId": company_id, "targetMonth": target_month},
			)
			or {}
		)
		if isinstance(result, dict) and isinstance(result.get("usages"), list):
			totals = {}
			for usage in result["usages"]:
				if not isinstance(usage, dict):
					continue
				for field, value in usage.items():
					if field == "productType" or isinstance(value, bool) or not isinstance(value, (int, float)):
						continue
					totals[field] = totals.get(field, 0) + value
			return totals
		return result if isinstance(result, dict) else {}
