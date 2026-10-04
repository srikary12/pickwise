# SPDX-License-Identifier: AGPL-3.0-only
"""The file pipeline against a real clamd (`make scan-check`). Skipped in `make test`:
ClamAV needs gigabytes of RAM and minutes to load its signatures."""

import os
import uuid

import pytest

from pickwise.platform.scanning import build_scanner, eicar_bytes
from pickwise.shared.settings import ScannerKind
from tests.integration.conftest import MakeApi
from tests.integration.support import Env, Tenant
from tests.integration.test_files import PDF, upload

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(os.environ.get("CLAMAV_E2E") != "1", reason="needs real clamd"),
]


async def test_real_clamd_rejects_eicar_and_passes_a_clean_file(
    make_api: MakeApi, env: Env, tenant: Tenant
) -> None:
    api = await (await make_api()).sign_in(await env.add_account(tenant, "employee"))
    scanner = build_scanner(env.settings, ScannerKind.CLAMAV)
    assert scanner.name == "clamav"
    assert await scanner.ping()

    infected = await upload(api, env, eicar_bytes(), "eicar.txt", "text/plain")
    outcome = await env.scan_file(tenant.id, uuid.UUID(infected["id"]), scanner)
    assert outcome.status == "infected"
    status = (await api.get(f"/v1/files/{infected['id']}")).json()
    assert status["scan_status"] == "infected"
    assert "eicar" in status["scan_detail"].lower()
    assert (await api.get(f"/v1/files/{infected['id']}/download")).status_code == 409

    clean = await upload(api, env, PDF)
    assert (await env.scan_file(tenant.id, uuid.UUID(clean["id"]), scanner)).status == "clean"
    assert (await api.get(f"/v1/files/{clean['id']}/download")).status_code == 200
