"""Peewee migrations -- 019_add_memory_action_log.py.

Some examples (model - class or model name)::

    > Model = migrator.orm['table_name']            # Return model in current state by name
    > Model = migrator.ModelClass                   # Return model in current state by name

    > migrator.sql(sql)                             # Run custom SQL
    > migrator.run(func, *args, **kwargs)           # Run python function with the given args
    > migrator.create_model(Model)                  # Create a model (could be used as decorator)
    > migrator.remove_model(model, cascade=True)    # Remove a model
    > migrator.add_fields(model, **fields)          # Add fields to a model
    > migrator.change_fields(model, **fields)       # Change fields
    > migrator.remove_fields(model, *field_names, cascade=True)
    > migrator.rename_field(model, old_field_name, new_field_name)
    > migrator.rename_table(model, new_table_name)
    > migrator.add_index(model, *col_names, unique=False)
    > migrator.add_not_null(model, *field_names)
    > migrator.add_default(model, field_name, default)
    > migrator.add_constraint(model, name, sql)
    > migrator.drop_index(model, *col_names)
    > migrator.drop_not_null(model, *field_names)
    > migrator.drop_constraints(model, *constraints)

"""

from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    @migrator.create_model
    class MemoryActionLog(pw.Model):
        id = pw.CharField(max_length=255, unique=True)
        user_id = pw.CharField(max_length=255)
        chat_id = pw.CharField(max_length=255, null=True)
        message_id = pw.CharField(max_length=255, null=True)
        status = pw.CharField(max_length=255)
        planner_model = pw.CharField(max_length=255, null=True)
        trigger_message = pw.TextField(null=True)
        planned_actions = pw.TextField(null=True)
        executed_actions = pw.TextField(null=True)
        error = pw.TextField(null=True)
        created_at = pw.BigIntegerField(null=False)

        class Meta:
            table_name = "memory_action_log"


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_model("memory_action_log")
