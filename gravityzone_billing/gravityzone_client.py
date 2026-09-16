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
MAX_PAGE_SIZE = 30  # documented GravityZone maximum for perPage
MIN_CALL_INTERVAL = 0.12  # ~8.3 req/sec, a safety margin under GravityZone's documented 10 req/sec cap
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

			response.raise_for_status()
			body = response.json()

			if body.get("error"):
				error = body["error"]
				raise GravityZoneError(
					code=error.get("code"), message=error.get("message", "unknown error"), data=error.get("data")
				)
			return body.get("result")

	def _paginate(self, service, method, params):
		page = 1
		while True:
			result = self._call(service, method, {**params, "page": page, "perPage": MAX_PAGE_SIZE})
			items = result.get("items", []) if isinstance(result, dict) else []
			yield from items

			pages_count = result.get("pagesCount", page) if isinstance(result, dict) else page
			if page >= pages_count:
				break
			page += 1

	def get_companies_list(self):
		"""List all MSP customer companies visible to this partner API key.

		Maps to the Network service ``getCompaniesList`` method, which is only
		populated for Partner-tier accounts (returns empty for direct/"Cloud
		Solutions" accounts).
		"""
		companies = []
		for item in self._paginate("network", "getCompaniesList", {}):
			companies.append(Company(id=str(item.get("id")), name=item.get("name", ""), raw=item))
		return companies

	def get_license_info(self, company_id):
		"""Current seat allocation/usage for a company (Licensing service)."""
		result = self._call("licensing", "getLicenseInfo", {"companyId": company_id})
		used = int(result.get("usedLicenses", result.get("used", 0)))
		allocated = result.get("additionalLicenses", result.get("allocatedLicenses"))
		return LicenseInfo(
			company_id=company_id,
			used_licenses=used,
			allocated_licenses=int(allocated) if allocated is not None else None,
			raw=result,
		)

	def get_monthly_usage(self, company_id, target_month):
		"""Actual monthly seat/mailbox consumption for a company.

		``target_month`` is a ``YYYY-MM`` string. This is Bitdefender's
		recommended source of truth for MSP billing reconciliation, as opposed
		to the point-in-time allocation returned by ``getLicenseInfo``.
		"""
		return self._call("licensing", "getMonthlyUsage", {"companyId": company_id, "targetMonth": target_month})
