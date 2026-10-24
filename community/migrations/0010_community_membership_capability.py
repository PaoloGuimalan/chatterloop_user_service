"""Enforce the membership invariant for every service sharing PostgreSQL."""

from django.db import migrations


def install_membership_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        CREATE FUNCTION community_reject_member_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM community_realm
                WHERE entity_id = NEW.entity_id AND type = 'community'
            ) THEN
                RAISE EXCEPTION 'Communities cannot be members of any realm'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER community_member_identity_guard
        BEFORE INSERT OR UPDATE OF entity_id ON community_member
        FOR EACH ROW EXECUTE FUNCTION community_reject_member_identity();

        CREATE FUNCTION community_reject_member_realm() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.type = 'community' AND EXISTS (
                SELECT 1 FROM community_member WHERE entity_id = NEW.entity_id
            ) THEN
                RAISE EXCEPTION 'A member identity cannot become a community'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER community_realm_identity_guard
        BEFORE INSERT OR UPDATE OF type, entity_id ON community_realm
        FOR EACH ROW EXECUTE FUNCTION community_reject_member_realm();
    """)


def remove_membership_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        DROP TRIGGER IF EXISTS community_member_identity_guard ON community_member;
        DROP TRIGGER IF EXISTS community_realm_identity_guard ON community_realm;
        DROP FUNCTION IF EXISTS community_reject_member_identity();
        DROP FUNCTION IF EXISTS community_reject_member_realm();
    """)


class Migration(migrations.Migration):
    dependencies = [("community", "0009_realm_community")]
    operations = [
        migrations.RunPython(install_membership_guard, remove_membership_guard)
    ]
