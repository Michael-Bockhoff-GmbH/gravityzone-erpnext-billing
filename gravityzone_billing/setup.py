"""Idempotent DocType creation, run once on install (see hooks.after_install).

DocTypes are created through the framework (not hand-written JSON) so that,
with developer_mode on, Frappe exports them to disk itself in the correct
shape for this Frappe/ERPNext version.
"""

import frappe


def after_install():
	create_gravityzone_settings()
	create_gravityzone_company()


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
