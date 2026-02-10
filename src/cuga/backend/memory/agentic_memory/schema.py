from datetime import datetime
from pydantic import BaseModel, Field
from pymilvus import CollectionSchema, FieldSchema, DataType
from sqlite3 import Cursor, Row


class Fact(BaseModel):
    """A statement about a person, place, or thing."""

    content: str = Field(
        description='A complete sentence describing a fact about the user, their personal preferences,'
        ' upcoming plans, professional details, and other miscellaneous information.'
    )
    category: str | None = Field(
        default=None,
        description='The category this fact belongs to (e.g., personal_details, professional_details, etc.)',
    )
    key: str | None = Field(default=None, description='Specific attribute name for this fact')
    value: str | None = Field(default=None, description='The actual value of the fact')
    # Made optional to support use cases that can't handle metadata
    metadata: dict | None = Field(
        default=None, description='Arbitrary metadata which is related to the fact.'
    )


class RecordedFact(Fact):
    """A statement about a person, place, or thing."""

    id: str = Field(description='The unique ID of a fact.')
    created_at: datetime = Field(description='The date and time the fact was created.')
    run_id: str | None = Field(default=None, description='The run associated with the fact.')


class Run(BaseModel):
    id: str = Field(description='The unique ID of a run.')
    created_at: datetime = Field(description='The date and time the run was created.')
    steps: list[RecordedFact] = Field(
        default_factory=list, description='A list of steps executed by the run.'
    )
    ended: bool = Field(default=False, description='Whether or not the run has ended.')

    @staticmethod
    def row_factory(cursor: Cursor, row: Row) -> 'Run':
        fields = [column[0] for column in cursor.description]
        return Run(**{k: v for k, v in zip(fields, row)})


fact_schema = CollectionSchema(
    fields=[
        # Keep it as an INT64 or else you won't be able to list all facts.
        FieldSchema(name='id', is_primary=True, auto_id=True, dtype=DataType.INT64),
        FieldSchema(name='content', dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name='created_at', dtype=DataType.INT64),
        # milvus-lite does not support nullable values. Use a default value like '' in place of `None`
        FieldSchema(name='run_id', dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name='embedding', dtype=DataType.FLOAT_VECTOR, dim=384),
        # metadata now stores category, key, value along with other metadata
        FieldSchema(name='metadata', dtype=DataType.JSON),
    ]
)


class Message(BaseModel):
    """A message in a chat log."""

    role: str = Field(
        description='The perspective from which the message is coming from. '
        'This can include but is not limited to the system, the assistant, the user, or a human.'
    )
    content: str = Field(description='The actual message.')


class Namespace(BaseModel):
    """Details of a namespace containing memories."""

    id: str = Field(description='The unique ID of a namespace.')
    created_at: datetime = Field(description='The time the namespace was created.')
    user_id: str | None = Field(default=None, description='The user which created the namespace.')
    agent_id: str | None = Field(default=None, description='The agent associated with the namespace.')
    app_id: str | None = Field(default=None, description='The application associated with the namespace.')
    num_entities: int | None = Field(
        default=None, description='The number of entities in the namespace. May not be accurate.'
    )

    @staticmethod
    def row_factory(cursor: Cursor, row: Row) -> 'Namespace':
        fields = [column[0] for column in cursor.description]
        return Namespace(**{k: v for k, v in zip(fields, row)})


Namespace.model_json_schema()
