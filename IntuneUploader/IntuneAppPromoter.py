#!/usr/local/autopkg/python
# -*- coding: utf-8 -*-

"""
This processor promotes apps in Intune based on the input variables. It will promote the app to the first group in the promotion_info list.
If the app has been promoted before, it will promote the app to the next group in the list, if the number of days since the last promotion is greater than or equal to the number of days in the previous ring.

Created by Tobias Almén
"""

import json
import os
import sys
from datetime import datetime

__all__ = ["IntuneAppPromoter"]

sys.path.insert(0, os.path.dirname(__file__))
from IntuneUploaderLib.IntuneUploaderBase import IntuneUploaderBase


class App:
    """App class"""

    def __init__(self, app_name, app_version):
        self.displayName = app_name
        self.primaryBundleVersion = app_version


class IntuneAppPromoter(IntuneUploaderBase):
    """This processor promotes an app to groups in Intune based on the input variables."""

    description = __doc__
    input_variables = {
        "display_name": {
            "required": True,
            "description": "The name of the app to assign.",
        },
        "blacklist_versions": {
            "required": False,
            "description": "If the app version is in this list, it will not be assigned. Can be a wildcard, for example, 5.*",
        },
        "promotion_info": {
            "required": True,
            "description": "An array of dicts containing information about the assignments and schedule.",
        },
    }
    output_variables = {
        "intuneapppromoter_summary_result": {
            "description": "Description of interesting results."
        }
    }

    def main(self):
        """Main process"""
        # Set variables
        self.BASE_ENDPOINT = (
            "https://graph.microsoft.com/beta/deviceAppManagement/mobileApps"
        )
        self.CLIENT_ID = self.env.get("CLIENT_ID")
        self.CLIENT_SECRET = self.env.get("CLIENT_SECRET")
        self.CLIENT_CERTIFICATE_PATH = self.env.get("CLIENT_CERTIFICATE_PATH")
        self.CLIENT_CERTIFICATE_PASSWORD = self.env.get("CLIENT_CERTIFICATE_PASSWORD")
        self.TENANT_ID = self.env.get("TENANT_ID")
        app_name = self.env.get("display_name")
        app_blacklist_versions = self.env.get("blacklist_versions")
        promotion_info = self.env.get("promotion_info")

        def ring_target(group):
            """What a promotion_info ring assigns to: its group id, or for an
            all_assignment ring (AllDevices / AllUsers) the target type."""
            if group.get("all_assignment"):
                return self._ALL_ASSIGNMENT_MAP.get(
                    group["all_assignment"], group["all_assignment"]
                )
            return group.get("group_id")

        def assignment_target(assignment):
            """The same for an assignment Intune returns."""
            target = assignment["target"]
            return target.get("groupId") or target.get("@odata.type")

        def promote_app(group):
            notes = {
                "promotion_date": date.strftime("%Y-%m-%d"),
                "ring": group.get("ring"),
            }
            self.assign_app(app, [group])
            notes["ring"] = group.get("ring")
            data = json.dumps(
                {
                    "notes": json.dumps(notes),
                    "@odata.type": intune_app.get("@odata.type"),
                }
            )
            self.makeapirequestPatch(
                f"{self.BASE_ENDPOINT}/{intune_app.get('id')}",
                self.token,
                None,
                data,
                204,
            )
            promotions.append({"version": app_version, "ring": group.get("ring")})
            assigned_groups[intune_app["id"]].add(ring_target(group))

        # Get access token
        self.token = self.obtain_accesstoken(
            self.CLIENT_ID,
            self.CLIENT_SECRET,
            self.TENANT_ID,
            cert_path=self.CLIENT_CERTIFICATE_PATH,
            cert_password=self.CLIENT_CERTIFICATE_PASSWORD,
        )

        # Check if promotion info is set
        if not promotion_info:
            self.output("No promotion info found, exiting.")
            return None

        promotions = []

        # Get matching apps
        intune_apps = self.get_matching_apps(app_name)
        # If no apps are found, exit
        if not intune_apps:
            self.output(f"No app found with name: {app_name}, exiting.")
            return None

        formatted_date_string = datetime.now().strftime("%Y-%m-%d")
        date = datetime.strptime(formatted_date_string, "%Y-%m-%d")

        def match_version(version, blacklist_versions):
            # Check if version is a wildcard
            for v in blacklist_versions:
                if v.endswith("*") and version.startswith(v[:-1]):
                    return True
            # Check if version is in blacklist
            if version in blacklist_versions:
                return True

            # If no match, return False
            return False

        def version(intune_app):
            return intune_app.get("primaryBundleVersion") or intune_app.get(
                "buildNumber"
            )

        def blacklisted(intune_app):
            return app_blacklist_versions is not None and match_version(
                version(intune_app), app_blacklist_versions
            )

        # Get all promotion targets (group ids, or all devices / all users)
        promotion_ids = [
            ring_target(group) for group in promotion_info if ring_target(group)
        ]
        # ring name for each target, for output
        ring_names = {
            ring_target(group): group.get("ring") or ring_target(group)
            for group in promotion_info
            if ring_target(group)
        }

        # Only the newest version is promoted through the rings: once a newer
        # version exists, older ones stay where they are, and lose their ring
        # assignments once the newest has every ring (see below).
        candidates = [a for a in intune_apps if not blacklisted(a)]
        newest = (
            max(candidates, key=lambda a: self.version_key(version(a)))
            if candidates
            else None
        )

        # each app's ring assignments as they stand, kept up to date as
        # promote_app() assigns more
        assigned_groups = {}
        ring_assignments = {}
        retired = []

        for intune_app in intune_apps:
            self.request = intune_app

            # Get assignments for app
            assignments = self.makeapirequest(
                f"{self.BASE_ENDPOINT}/{intune_app.get('id')}/assignments",
                self.token,
                None,
            )
            current_group_ids = [
                assignment_target(c)
                for c in assignments["value"]
                if assignment_target(c)
            ]
            assigned_groups[intune_app["id"]] = set(current_group_ids)
            ring_assignments[intune_app["id"]] = [
                c
                for c in assignments["value"]
                if assignment_target(c) in promotion_ids
            ]

            app_version = version(intune_app)
            app = App(app_name, app_version)

            if blacklisted(intune_app):
                self.output(
                    f"App version {version(intune_app)} is blacklisted, skipping version."
                )
                continue
            if intune_app is not newest:
                self.output(
                    f"{intune_app.get('displayName')} {app_version} is older than "
                    f"{version(newest)}, not promoting it."
                )
                continue
            # If all promotion group ids are in current group ids, exits
            if all(id in current_group_ids for id in promotion_ids):
                self.output(
                    f"{intune_app.get('displayName')} {app_version} is already assigned to all groups, skipping."
                )
                # the next matching app (another version) may still need
                # promoting
                continue

            # If app has not been promoted before, assign to first group in promotion_info
            if not intune_app.get("notes"):
                promote_app(promotion_info[0])
            # If app has been promoted before, check if it is time to promote again
            else:
                # Get days since last promotion and previous ring
                notes_data = json.loads(intune_app.get("notes"))
                promote_date = notes_data.get("promotion_date")
                delta = (date - datetime.strptime(promote_date, "%Y-%m-%d")).days
                previous_ring = notes_data.get("ring")
                # a ring without days (typically the last one) counts as 0
                previous_ring_days = sum(
                    [
                        group.get("days") or 0
                        for group in promotion_info
                        if group.get("ring") == previous_ring
                    ]
                )

                for group in promotion_info:
                    # If the delta is greater than or equal to the previous ring days, promote
                    if (
                        ring_target(group) not in current_group_ids
                        and delta >= previous_ring_days
                    ):
                        promote_app(group)
                        break

        # A ring the newest version is assigned to is taken off every older
        # version, so each ring group only targets one version. Once the
        # newest version has every ring, older versions have none left.
        # Other assignments they have (e.g. uninstall) are left alone.
        if newest:
            newest_key = self.version_key(version(newest))
            newest_targets = assigned_groups.get(newest["id"], set())
            for intune_app in intune_apps:
                if self.version_key(version(intune_app)) >= newest_key:
                    continue
                removed = []
                for assignment in ring_assignments.get(intune_app["id"], []):
                    target = assignment_target(assignment)
                    if target not in newest_targets:
                        continue
                    self.makeapirequestDelete(
                        f"{self.BASE_ENDPOINT}/{intune_app['id']}/assignments/"
                        f"{assignment['id']}",
                        self.token,
                        status_code=204,
                    )
                    removed.append(ring_names.get(target, target))
                if removed:
                    self.output(
                        f"{intune_app.get('displayName')} {version(intune_app)}: "
                        f"removed ring(s) {', '.join(removed)}, now assigned to "
                        f"{version(newest)}."
                    )
                    retired.append(
                        f"{version(intune_app)} ({', '.join(removed)})"
                    )

        self.env["intuneapppromoter_summary_result"] = {
            "summary_text": "Summary of IntuneAppPromoter results:",
            "report_fields": [
                "app name",
                "promotions",
                "retired versions",
                "blacklisted versions",
            ],
            "data": {
                "app name": app_name,
                "retired versions": ", ".join(retired),
                "promotions": (
                    ", ".join(
                        [
                            f"{promotion.get('version')} ({promotion.get('ring')})"
                            for promotion in promotions
                        ]
                    )
                    if len(promotions) > 0
                    else ""
                ),
                "blacklisted versions": (
                    ", ".join(app_blacklist_versions)
                    if app_blacklist_versions is not None
                    else ""
                ),
            },
        }


if __name__ == "__main__":
    processor = IntuneAppPromoter()
    processor.execute_shell()
