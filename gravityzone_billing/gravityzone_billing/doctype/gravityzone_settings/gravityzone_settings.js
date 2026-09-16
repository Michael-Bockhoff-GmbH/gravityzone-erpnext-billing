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
				const { created, updated } = r.message || {};
				frappe.msgprint(
					__("Created {0} and updated {1} GravityZone Company record(s).", [created, updated])
				);
				frappe.set_route("List", "GravityZone Company");
			});
		});

		frm.add_custom_button(__("Sync Licenses Now"), () => {
			frappe.call({
				method: "gravityzone_billing.sync.sync_now",
				freeze: true,
				freeze_message: __("Syncing license counts..."),
			}).then(() => {
				frappe.msgprint(__("Sync complete. Check the GravityZone Company list for results."));
			});
		});

		if (frm.doc.license_metric === "Per-Product Monthly Usage") {
			frm.add_custom_button(__("Manage Product Mappings"), () => {
				frappe.set_route("List", "GravityZone Product Mapping");
			});
		}
	},
});
