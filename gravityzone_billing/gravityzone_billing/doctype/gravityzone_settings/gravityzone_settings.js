// Copyright (c) 2026, Michael Bockhoff GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("GravityZone Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Discover Companies"), () => {
			frappe.call({
				method: "gravityzone_billing.sync.discover_companies",
				freeze: true,
				freeze_message: __("Fetching companies from GravityZone..."),
			}).then((r) => {
				const { created = 0, updated = 0, sub_companies = 0, excluded_parents = 0 } = r.message || {};
				let message = __("Created {0} and updated {1} GravityZone Company record(s), {2} of them sub-companies.", [
					created,
					updated,
					sub_companies,
				]);
				if (excluded_parents) {
					message +=
						"<br><br>" +
						__(
							"{0} company(ies) with sub-companies were set to Exclude from Sync, because their usage counters appear to be the sum of their sub-companies' — each sub-company is synced and billed individually instead.",
							[excluded_parents]
						);
				}
				frappe.msgprint(message);
				frappe.set_route("List", "GravityZone Company");
			});
		});

		frm.add_custom_button(__("Create Missing ERPNext Customers"), () => {
			frappe.confirm(
				__(
					"Create an ERPNext Customer for every GravityZone Company that doesn't have one yet? Companies excluded from sync are skipped."
				),
				() => {
					frappe.call({
						method: "gravityzone_billing.sync.create_missing_erpnext_customers",
						freeze: true,
						freeze_message: __("Creating Customers..."),
					}).then((r) => {
						const { created = 0, linked = 0 } = r.message || {};
						frappe.msgprint(
							created || linked
								? __("Created {0} ERPNext Customer(s), linked {1} existing one(s) with the same name.", [created, linked])
								: __("Nothing to do: every GravityZone Company already has an ERPNext Customer or is excluded from sync.")
						);
						frappe.set_route("List", "GravityZone Company");
					});
				}
			);
		});

		frm.add_custom_button(__("Discover License Types"), () => {
			frappe.call({
				method: "gravityzone_billing.sync.discover_license_types",
				freeze: true,
				freeze_message: __("Reading licenses from GravityZone..."),
			}).then((r) => {
				const { companies = 0, types = 0, created = 0 } = r.message || {};
				frappe.msgprint({
					title: __("GravityZone License Types"),
					message:
						__("Read {0} companies, found {1} license type(s) in use, created {2} new Product Mapping row(s).", [
							companies,
							types,
							created,
						]) +
						"<br><br>" +
						__(
							"Open the Product Mapping list, pick the ERPNext Subscription Plan / Item for each type you bill, then enable it. Note that Endpoint Security and the MSP package counters count the same seats — enable only the ones you actually invoice, or each seat is billed twice."
						),
					indicator: "green",
				});
				frappe.set_route("List", "GravityZone Product Mapping");
			});
		});

		frm.add_custom_button(__("Sync Licenses Now"), () => {
			frappe.call({
				method: "gravityzone_billing.sync.sync_now",
				freeze: true,
				freeze_message: __("Syncing license counts..."),
			}).then((r) => {
				const { synced = 0, skipped = 0, failed = 0 } = r.message || {};
				let message = __("Synced {0}, skipped {1}, failed {2}.", [synced, skipped, failed]);
				if (skipped) {
					message += " " + __("Skipped companies have no ERPNext Customer assigned yet or are excluded from sync.");
				}
				if (failed) {
					message += " " + __("See Error Log for the failures.");
				}
				frappe.msgprint({
					title: __("GravityZone Sync"),
					message,
					indicator: failed ? "red" : skipped ? "orange" : "green",
				});
			});
		});

		if (frm.doc.license_metric === "Per-Product Monthly Usage") {
			frm.add_custom_button(__("Manage Product Mappings"), () => {
				frappe.set_route("List", "GravityZone Product Mapping");
			});
		}
	},
});
