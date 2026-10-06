# SPDX-License-Identifier: AGPL-3.0-only
import uuid

from pydantic import BaseModel, ConfigDict, Field


class HitOut(BaseModel):
    kind: str
    id: uuid.UUID
    title: str
    subtitle: str | None
    link: str = Field(description="An app-relative path")


class GroupOut(BaseModel):
    kind: str
    label: str
    hits: list[HitOut]


class SearchOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "groups": [
                        {
                            "kind": "user",
                            "label": "People with a sign-in",
                            "hits": [
                                {
                                    "kind": "user",
                                    "id": "01928f6e-…",
                                    "title": "Asha Rao",
                                    "subtitle": "asha@acme.test",
                                    "link": "/admin/users",
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    )

    groups: list[GroupOut]
