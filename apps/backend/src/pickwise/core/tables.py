# SPDX-License-Identifier: AGPL-3.0-only
"""Lightweight SQLAlchemy Core handles on the core tables, for building scoped queries.

Only the columns the query builders need. The tables are created by migrations 0006 and 0007.
"""

from sqlalchemy import column, table

employees = table(
    "employees",
    column("id"),
    column("membership_id"),
    column("status"),
    column("employee_code"),
    column("display_name"),
    column("first_name"),
    column("middle_name"),
    column("last_name"),
    column("preferred_name"),
    column("work_email"),
    column("work_phone"),
    column("date_of_joining"),
    column("original_hire_date"),
    column("probation_end_date"),
    column("confirmation_date"),
    column("date_of_exit"),
    column("photo_file_id"),
    column("custom_fields"),
    column("row_version"),
    schema="core",
)
current_jobs = table(
    "employee_job_records_current",
    column("employee_id"),
    column("legal_entity_id"),
    column("location_id"),
    column("department_id"),
    column("designation_id"),
    column("manager_employee_id"),
    column("employment_type"),
    schema="core",
)
departments = table(
    "departments",
    column("id"),
    column("name"),
    column("path"),
    column("head_employee_id"),
    schema="core",
)
designations = table("designations", column("id"), column("name"), schema="core")
locations = table("locations", column("id"), column("name"), schema="core")
hierarchy = table(
    "employee_hierarchy",
    column("ancestor_employee_id"),
    column("descendant_employee_id"),
    column("depth"),
    schema="core",
)
