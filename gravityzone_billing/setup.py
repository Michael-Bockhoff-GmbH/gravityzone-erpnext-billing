"""Idempotent DocType creation, run once on install (see hooks.after_install).

DocTypes are created through the framework (not hand-written JSON) so that,
with developer_mode on, Frappe exports them to disk itself in the correct
shape for this Frappe/ERPNext version.
"""

import frappe


def after_install():
	create_gravityzone_settings()
	create_gravityzone_company()
	create_gravityzone_sync_history()
	add_review_threshold_fields_to_settings()
	add_review_fields_to_company()


def create_gravityzone_settings():
	if frappe.db.exists("DocType", "GravityZone Settings"):
		return

	frappe.get_doc(
		{
			"doctype": "DocType",
			"name": "GravityZone Settings",
			"module": "GravityZone Billing",
			"custom": 0,
			"issingle": 1,
			"track_changes": 1,
			"fields": [
				{
					"fieldname": "connection_section",
					"fieldtype": "Section Break",
					"label": "GravityZone Connection",
				},
				{
					"fieldname": "api_key",
					"fieldtype": "Password",
					"label": "API Key",
					"description": (
						"Control Center &gt; My Account &gt; API keys. The key needs access to the "
						"Network and Licensing APIs (Partner tier) to list companies and read license counts."
					),
				},
				{
					"fieldname": "base_url",
					"fieldtype": "Data",
					"label": "API Base URL",
					"default": "https://cloud.gravityzone.bitdefender.com/api/v1.0/jsonrpc",
					"reqd": 1,
				},
				{"fieldname": "column_break_connection", "fieldtype": "Column Break"},
				{
					"fieldname": "license_metric",
					"fieldtype": "Select",
					"label": "License Metric",
					"options": "License Info\nMonthly Usage",
					"default": "License Info",
					"description": (
						"License Info = current allocated seats (getLicenseInfo). Monthly Usage = actual "
						"monthly consumption (getMonthlyUsage) — Bitdefender's recommended source for "
						"MSP billing reconciliation."
					),
				},
				{
					"fieldname": "sync_enabled",
					"fieldtype": "Check",
					"label": "Enable Daily Scheduled Sync",
					"default": "1",
				},
				{"fieldname": "billing_section", "fieldtype": "Section Break", "label": "Billing Defaults"},
				{
					"fieldname": "default_company",
					"fieldtype": "Link",
					"options": "Company",
					"label": "Default Company",
					"description": "Used when creating a new Subscription for a customer that doesn't have one yet.",
				},
				{
					"fieldname": "subscription_plan",
					"fieldtype": "Link",
					"options": "Subscription Plan",
					"label": "Subscription Plan",
					"reqd": 1,
					"description": (
						"The Subscription Plan representing one GravityZone seat (its Item rate is "
						"multiplied by the license count on each customer's Subscription)."
					),
				},
			],
			"permissions": [
				{
					"role": "System Manager",
					"read": 1,
					"write": 1,
					"create": 1,
					"delete": 1,
					"print": 1,
					"email": 1,
					"share": 1,
				}
			],
		}
	).insert(ignore_permissions=True)


def create_gravityzone_company():
	if frappe.db.exists("DocType", "GravityZone Company"):
		return

	frappe.get_doc(
		{
			"doctype": "DocType",
			"name": "GravityZone Company",
			"module": "GravityZone Billing",
			"custom": 0,
			"autoname": "field:gz_company_id",
			"title_field": "gz_company_name",
			"sort_field": "modified",
			"sort_order": "DESC",
			"fields": [
				{
					"fieldname": "gz_company_id",
					"fieldtype": "Data",
					"label": "GravityZone Company ID",
					"reqd": 1,
					"unique": 1,
				},
				{
					"fieldname": "gz_company_name",
					"fieldtype": "Data",
					"label": "GravityZone Company Name",
				},
				{"fieldname": "column_break_mapping", "fieldtype": "Column Break"},
				{
					"fieldname": "customer",
					"fieldtype": "Link",
					"options": "Customer",
					"label": "ERPNext Customer",
					"reqd": 1,
				},
				{
					"fieldname": "min_qty",
					"fieldtype": "Int",
					"label": "Minimum Billable Seats",
					"default": "0",
					"non_negative": 1,
					"description": "Floor applied to the synced license count, e.g. for a minimum-commit contract.",
				},
				{
					"fieldname": "status_section",
					"fieldtype": "Section Break",
					"label": "Sync Status",
					"collapsible": 1,
				},
				{
					"fieldname": "subscription",
					"fieldtype": "Link",
					"options": "Subscription",
					"label": "Subscription",
					"read_only": 1,
				},
				{
					"fieldname": "last_synced_qty",
					"fieldtype": "Int",
					"label": "Last Synced License Count",
					"read_only": 1,
				},
				{"fieldname": "column_break_status", "fieldtype": "Column Break"},
				{
					"fieldname": "last_synced_on",
					"fieldtype": "Datetime",
					"label": "Last Synced On",
					"read_only": 1,
				},
				{
					"fieldname": "last_sync_message",
					"fieldtype": "Small Text",
					"label": "Last Sync Message",
					"read_only": 1,
				},
			],
			"permissions": [
				{
					"role": "System Manager",
					"read": 1,
					"write": 1,
					"create": 1,
					"delete": 1,
					"print": 1,
					"email": 1,
					"share": 1,
					"report": 1,
					"export": 1,
				}
			],
		}
	).insert(ignore_permissions=True)


def create_gravityzone_sync_history():
	if frappe.db.exists("DocType", "GravityZone Sync History"):
		return

	frappe.get_doc(
		{
			"doctype": "DocType",
			"name": "GravityZone Sync History",
			"module": "GravityZone Billing",
			"custom": 0,
			"istable": 1,
			"editable_grid": 1,
			"fields": [
				{
					"fieldname": "timestamp",
					"fieldtype": "Datetime",
					"label": "Timestamp",
					"in_list_view": 1,
					"reqd": 1,
				},
				{
					"fieldname": "previous_qty",
					"fieldtype": "Int",
					"label": "Previous Qty",
					"in_list_view": 1,
				},
				{
					"fieldname": "new_qty",
					"fieldtype": "Int",
					"label": "New Qty",
					"in_list_view": 1,
				},
				{
					"fieldname": "outcome",
					"fieldtype": "Select",
					"label": "Outcome",
					"options": "Created\nUpdated\nUnchanged\nFlagged for Review\nApproved",
					"in_list_view": 1,
				},
				{
					"fieldname": "message",
					"fieldtype": "Small Text",
					"label": "Message",
					"in_list_view": 1,
				},
			],
			"permissions": [
				{"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1}
			],
		}
	).insert(ignore_permissions=True)


def add_review_threshold_fields_to_settings():
	doc = frappe.get_doc("DocType", "GravityZone Settings")
	existing = {f.fieldname for f in doc.fields}
	changed = False

	if "review_thresholds_section" not in existing:
		doc.append(
			"fields",
			{
				"fieldname": "review_thresholds_section",
				"fieldtype": "Section Break",
				"label": "Automatic Review Thresholds",
			},
		)
		changed = True

	if "review_threshold_percent" not in existing:
		doc.append(
			"fields",
			{
				"fieldname": "review_threshold_percent",
				"fieldtype": "Percent",
				"label": "Flag Changes Over (%)",
				"default": "50",
				"description": (
					"A synced license-count change is held for manual review instead of applied "
					"automatically when it exceeds BOTH this percentage and the seat count below."
				),
			},
		)
		changed = True

	if "column_break_review_thresholds" not in existing:
		doc.append("fields", {"fieldname": "column_break_review_thresholds", "fieldtype": "Column Break"})
		changed = True

	if "review_threshold_min_seats" not in existing:
		doc.append(
			"fields",
			{
				"fieldname": "review_threshold_min_seats",
				"fieldtype": "Int",
				"label": "Flag Changes Over (seats)",
				"default": "5",
				"non_negative": 1,
			},
		)
		changed = True

	if changed:
		doc.save(ignore_permissions=True)


def add_review_fields_to_company():
	doc = frappe.get_doc("DocType", "GravityZone Company")
	existing = {f.fieldname for f in doc.fields}
	changed = False

	if "needs_review" not in existing:
		doc.append(
			"fields",
			{
				"fieldname": "needs_review",
				"fieldtype": "Check",
				"label": "Needs Review",
				"description": (
					"A synced license-count change exceeded the configured review threshold and was "
					"not applied automatically. See Pending License Count and Sync History below."
				),
			},
		)
		changed = True

	if "pending_qty" not in existing:
		doc.append(
			"fields",
			{
				"fieldname": "pending_qty",
				"fieldtype": "Int",
				"label": "Pending License Count",
				"read_only": 1,
				"depends_on": "eval:doc.needs_review",
				"description": "The synced value awaiting manual approval.",
			},
		)
		changed = True

	if "sync_history_section" not in existing:
		doc.append(
			"fields",
			{
				"fieldname": "sync_history_section",
				"fieldtype": "Section Break",
				"label": "Sync History",
				"collapsible": 1,
			},
		)
		changed = True

	if "sync_history" not in existing:
		doc.append(
			"fields",
			{
				"fieldname": "sync_history",
				"fieldtype": "Table",
				"options": "GravityZone Sync History",
				"label": "Sync History",
			},
		)
		changed = True

	if changed:
		doc.save(ignore_permissions=True)
