# SPDX-License-Identifier: AGPL-3.0-only
"""Encrypted columns, blind indexes and key rotation against a scratch table."""

import base64
import os
import uuid
from collections.abc import Iterator
from typing import Any, cast

import psycopg
import pytest
from sqlalchemy import String, insert, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from pickwise.platform.crypto import (
    BLIND_INDEX,
    DATA,
    ENCRYPTED_FIELDS,
    Ciphertext,
    CiphertextError,
    EncryptedField,
    EncryptedStr,
    EnvKek,
    FieldCryptoError,
    Keyring,
    blind_index,
    blind_lookup_values,
    field_crypto,
    key_version_of,
    load_tenant_keyring,
    normalize_identifier,
    reveal,
)
from pickwise.platform.crypto.rotation import (
    add_tenant_key_version,
    count_on_old_data_keys,
    rebuild_blind_index,
    reencrypt_field,
    retire_rotating,
    rewrap_all,
)
from pickwise.shared.context import ActorType, RequestContext
from pickwise.shared.db import Database, ops_task
from pickwise.shared.models import Base as AppBase
from pickwise.shared.models import TenantMixin
from tests.integration.support import Env, Tenant

pytestmark = pytest.mark.db

SCHEMA = "crypto_probe"


class ProbeBase(DeclarativeBase):
    pass


class Person(TenantMixin, ProbeBase):
    __tablename__ = "people"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    name: Mapped[str | None] = mapped_column(String)
    pan: Mapped[Any] = mapped_column("pan_enc", EncryptedStr)
    pan_bidx: Mapped[bytes | None] = mapped_column()


assert AppBase.metadata is not ProbeBase.metadata

FIELD = EncryptedField(SCHEMA, "people", "pan_enc", "pan_bidx")
PAN = "ABCDE1234F"


@pytest.fixture(scope="module", autouse=True)
def scratch_table() -> Iterator[None]:
    """A tenant table made the way migrations make them, dropped when the module ends."""
    conn = psycopg.connect(
        host=os.environ.get("DATABASE_HOST", "postgres"),
        dbname=os.environ.get("DATABASE_NAME", "pickwise"),
        user="pickwise_migrator",
        password=os.environ["PG_MIGRATOR_PASSWORD"],
        autocommit=True,
    )
    conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    conn.execute(f"CREATE SCHEMA {SCHEMA}")
    conn.execute(
        f"""CREATE TABLE {SCHEMA}.people (
            tenant_id uuid NOT NULL DEFAULT platform.current_tenant_id()
                REFERENCES platform.tenants (id),
            id uuid NOT NULL DEFAULT uuidv7(),
            name text, pan_enc bytea, pan_bidx bytea,
            PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, pan_bidx))"""
    )
    conn.execute(f"SELECT platform.apply_tenant_policies('{SCHEMA}')")
    conn.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO pickwise_app, pickwise_ops")
    ENCRYPTED_FIELDS.register(FIELD)
    yield
    ENCRYPTED_FIELDS.unregister(FIELD)
    conn.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
    conn.close()


def ctx(tenant: Tenant) -> RequestContext:
    return RequestContext(ActorType.USER, tenant.id, tenant.admin.user_id)


@ops_task
async def keyring_of(env: Env, tenant: Tenant) -> Keyring:
    async with env.db.ops_session() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)})
        return await load_tenant_keyring(s, env.kek, tenant.id)


async def add_person(
    db: Database, env: Env, tenant: Tenant, pan: str | None = PAN, name: str = "Asha"
) -> uuid.UUID:
    keyring = await keyring_of(env, tenant)
    async with db.tenant_session(ctx(tenant)) as session:
        with field_crypto(keyring, tenant.id):
            person = Person(name=name, pan=pan)
            if pan is not None:
                person.pan_bidx = blind_index(
                    keyring.active_key(BLIND_INDEX).material, normalize_identifier(pan)
                )
            session.add(person)
            await session.flush()
            assert person.pan == pan  # the instance still holds the plaintext
            return person.id


async def stored_ciphertext(env: Env, person_id: uuid.UUID) -> bytes:
    ((value,),) = await env.sql(f"SELECT pan_enc FROM {SCHEMA}.people WHERE id = :i", i=person_id)
    return bytes(value)


async def load(
    db: Database, tenant: Tenant, person_id: uuid.UUID, keyring: Keyring | None
) -> Person:
    async with db.tenant_session(ctx(tenant)) as session:
        if keyring is None:
            return (
                await session.execute(select(Person).where(Person.id == person_id))
            ).scalar_one()
        with field_crypto(keyring, tenant.id):
            return (
                await session.execute(select(Person).where(Person.id == person_id))
            ).scalar_one()


# --- round trip and binding -------------------------------------------------------------------


async def test_value_round_trips_and_is_stored_encrypted(
    api_db: Database, env: Env, tenant: Tenant
) -> None:
    person_id = await add_person(api_db, env, tenant)
    stored = await stored_ciphertext(env, person_id)
    assert PAN.encode() not in stored
    assert key_version_of(stored) == 1

    keyring = await keyring_of(env, tenant)
    assert (await load(api_db, tenant, person_id, keyring)).pan == PAN


async def test_without_keys_the_value_stays_opaque(
    api_db: Database, env: Env, tenant: Tenant
) -> None:
    person_id = await add_person(api_db, env, tenant)
    person = await load(api_db, tenant, person_id, None)
    assert isinstance(person.pan, Ciphertext)
    assert PAN not in repr(person.pan)
    assert person.name == "Asha"  # listings never need keys
    with field_crypto(await keyring_of(env, tenant), tenant.id):
        reveal(person)
    assert cast(Any, person).pan == PAN


async def test_a_ciphertext_moved_to_another_row_fails_to_decrypt(
    api_db: Database, env: Env, tenant: Tenant
) -> None:
    victim = await add_person(api_db, env, tenant, "VICTM9999V", "Victim")
    attacker_row = await add_person(api_db, env, tenant, "ATTCK1111A", "Attacker")
    await env.sql(
        f"UPDATE {SCHEMA}.people SET pan_enc = (SELECT pan_enc FROM {SCHEMA}.people WHERE id = :v) "
        "WHERE id = :a",
        v=victim,
        a=attacker_row,
    )
    keyring = await keyring_of(env, tenant)
    with pytest.raises(CiphertextError):
        await load(api_db, tenant, attacker_row, keyring)


async def test_a_ciphertext_does_not_open_under_another_tenant(
    api_db: Database, env: Env, tenant: Tenant
) -> None:
    other = await env.create_tenant()
    person_id = await add_person(api_db, env, tenant)
    ciphertext = Ciphertext(await stored_ciphertext(env, person_id))
    with field_crypto(await keyring_of(env, other), other.id), pytest.raises(CiphertextError):
        ciphertext.open(f"{SCHEMA}.people", "pan_enc", person_id)


async def test_plaintext_never_reaches_the_database(
    api_db: Database, env: Env, tenant: Tenant
) -> None:
    keyring = await keyring_of(env, tenant)
    with field_crypto(keyring, tenant.id):
        async with api_db.tenant_session(ctx(tenant)) as session:
            with pytest.raises((FieldCryptoError, StatementError)):
                await session.execute(insert(Person).values(name="x", pan_enc=PAN))

    async def write_without_keys() -> None:  # writing needs keys too
        async with api_db.tenant_session(ctx(tenant)) as session:
            session.add(Person(name="no keys", pan=PAN))
            await session.flush()

    with pytest.raises((FieldCryptoError, StatementError)):
        await write_without_keys()


async def test_updates_reencrypt_only_what_changed(
    api_db: Database, env: Env, tenant: Tenant
) -> None:
    person_id = await add_person(api_db, env, tenant)
    before = await stored_ciphertext(env, person_id)
    keyring = await keyring_of(env, tenant)
    async with api_db.tenant_session(ctx(tenant)) as session:
        with field_crypto(keyring, tenant.id):
            person = (
                await session.execute(select(Person).where(Person.id == person_id))
            ).scalar_one()
            person.name = "Renamed"
            await session.flush()
    assert await stored_ciphertext(env, person_id) == before
    async with api_db.tenant_session(ctx(tenant)) as session:
        with field_crypto(keyring, tenant.id):
            person = (
                await session.execute(select(Person).where(Person.id == person_id))
            ).scalar_one()
            person.pan = "ZZZZZ0000Z"
            await session.flush()
            assert person.pan == "ZZZZZ0000Z"
    assert await stored_ciphertext(env, person_id) != before
    assert (await load(api_db, tenant, person_id, keyring)).pan == "ZZZZZ0000Z"


# --- blind index ----------------------------------------------------------------------------------


async def test_blind_index_enforces_uniqueness_across_formatting(
    api_db: Database, env: Env, tenant: Tenant
) -> None:
    await add_person(api_db, env, tenant, "KLMNO4321P")
    with pytest.raises(IntegrityError):
        await add_person(api_db, env, tenant, "klmno 4321-p")  # same value, different typing


async def test_blind_index_lookup_finds_the_row_and_is_per_tenant(
    api_db: Database, env: Env, tenant: Tenant
) -> None:
    person_id = await add_person(api_db, env, tenant, "QRSTU5678Q")
    other = await env.create_tenant()
    await add_person(api_db, env, other, "QRSTU5678Q", "Same PAN, other tenant")  # no clash

    keyring = await keyring_of(env, tenant)
    async with api_db.tenant_session(ctx(tenant)) as session:
        found = (
            (
                await session.execute(
                    select(Person.id).where(
                        Person.pan_bidx.in_(blind_lookup_values(keyring, "qrstu 5678 q"))
                    )
                )
            )
            .scalars()
            .all()
        )
    assert found == [person_id]
    other_keyring = await keyring_of(env, other)
    assert blind_lookup_values(keyring, "QRSTU5678Q") != blind_lookup_values(
        other_keyring, "QRSTU5678Q"
    )


# --- rotation -------------------------------------------------------------------------------------


async def test_data_key_rotation_moves_values_then_retires_the_old_key(
    api_db: Database, env: Env
) -> None:
    tenant = await env.create_tenant()
    old_rows = [await add_person(api_db, env, tenant, f"AAAAA000{i}A") for i in range(3)]

    @ops_task
    async def rotate() -> int:
        async with env.db.ops_session() as s:
            return await add_tenant_key_version(s, env.kek, tenant.id, DATA)

    assert await rotate() == 2
    keyring = await keyring_of(env, tenant)
    assert keyring.active_key(DATA).version == 2
    assert set(keyring.keys[DATA]) == {1, 2}  # the old key stays readable

    new_row = await add_person(api_db, env, tenant, "BBBBB1111B")
    assert key_version_of(await stored_ciphertext(env, new_row)) == 2
    assert key_version_of(await stored_ciphertext(env, old_rows[0])) == 1
    assert (await load(api_db, tenant, old_rows[0], keyring)).pan == "AAAAA0000A"

    @ops_task
    async def reencrypt_and_retire() -> tuple[int, int, int]:
        async with env.db.ops_session() as s:
            changed = await reencrypt_field(s, keyring, tenant.id, FIELD)
        async with env.db.ops_session() as s:
            remaining = await count_on_old_data_keys(s, tenant.id, FIELD, 2)
        async with env.db.ops_session() as s:
            retired = await retire_rotating(s, tenant.id, DATA)
        return changed, remaining, retired

    assert await reencrypt_and_retire() == (3, 0, 1)
    final = await keyring_of(env, tenant)
    assert set(final.keys[DATA]) == {2}
    for index, row in enumerate(old_rows):
        assert key_version_of(await stored_ciphertext(env, row)) == 2
        assert (await load(api_db, tenant, row, final)).pan == f"AAAAA000{index}A"


async def test_a_second_rotation_cannot_start_until_the_first_finishes(env: Env) -> None:
    tenant = await env.create_tenant()

    @ops_task
    async def rotate() -> int:
        async with env.db.ops_session() as s:
            return await add_tenant_key_version(s, env.kek, tenant.id, DATA)

    await rotate()
    with pytest.raises(RuntimeError, match="already in progress"):
        await rotate()


async def test_blind_index_rotation_looks_up_under_both_keys_then_rebuilds(
    api_db: Database, env: Env
) -> None:
    tenant = await env.create_tenant()
    row = await add_person(api_db, env, tenant, "CCCCC2222C")

    @ops_task
    async def rotate() -> int:
        async with env.db.ops_session() as s:
            return await add_tenant_key_version(s, env.kek, tenant.id, BLIND_INDEX)

    assert await rotate() == 2
    keyring = await keyring_of(env, tenant)
    values = blind_lookup_values(keyring, "CCCCC2222C")
    assert len(values) == 2  # every non-retired key

    async def lookup(ring: Keyring) -> list[uuid.UUID]:
        async with api_db.tenant_session(ctx(tenant)) as session:
            return list(
                (
                    await session.execute(
                        select(Person.id).where(
                            Person.pan_bidx.in_(blind_lookup_values(ring, "CCCCC2222C"))
                        )
                    )
                ).scalars()
            )

    assert await lookup(keyring) == [row]  # the old index still matches during rotation

    @ops_task
    async def rebuild_and_retire() -> int:
        async with env.db.ops_session() as s:
            rebuilt = await rebuild_blind_index(s, keyring, tenant.id, FIELD)
        async with env.db.ops_session() as s:
            await retire_rotating(s, tenant.id, BLIND_INDEX)
        return rebuilt

    assert await rebuild_and_retire() == 1
    final = await keyring_of(env, tenant)
    assert len(blind_lookup_values(final, "CCCCC2222C")) == 1
    assert await lookup(final) == [row]
    # A duplicate is still caught after the rotation.
    with pytest.raises(IntegrityError):
        await add_person(api_db, env, tenant, "ccccc-2222-c")


async def test_kek_rotation_rewraps_every_key_and_data_still_opens(
    api_db: Database, env: Env, tenant: Tenant
) -> None:
    person_id = await add_person(api_db, env, tenant)
    old_kek = env.kek
    new_kek = EnvKek.from_base64(base64.b64encode(os.urandom(32)).decode())

    @ops_task
    async def rewrap(old: Any, new: Any) -> dict[str, int]:
        async with env.db.ops_session() as s:
            return await rewrap_all(s, old, new)

    counts = await rewrap(old_kek, new_kek)
    assert counts["tenant_keys"] >= 2
    try:

        @ops_task
        async def load_with(kek: Any) -> Keyring:
            async with env.db.ops_session() as s:
                return await load_tenant_keyring(s, kek, tenant.id)

        keyring = await load_with(new_kek)
        assert (await load(api_db, tenant, person_id, keyring)).pan == PAN
        with pytest.raises(Exception):  # noqa: B017, PT011 - wrong KEK: authentication fails
            await load_with(old_kek)
    finally:
        await rewrap(new_kek, old_kek)  # leave the shared test database as we found it
    assert (await load(api_db, tenant, person_id, await keyring_of(env, tenant))).pan == PAN
