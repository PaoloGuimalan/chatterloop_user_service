"""Reserve the new human-review route without replacing an operator's policy."""
import unicodedata
from django.db import migrations


def reserve_review_route(apps, schema_editor):
    Variable = apps.get_model('core', 'Variable')
    row = Variable.objects.using(schema_editor.connection.alias).select_for_update().filter(key='reserved_profile_paths').first()
    if row is None or not isinstance(row.value, dict):
        return  # The existing fallback includes this route when policy is unavailable.
    paths = row.value.get('paths')
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        return
    if any(unicodedata.normalize('NFKC', path).strip().casefold() == 'realm-review' for path in paths):
        return
    row.value = {**row.value, 'paths': [*paths, 'realm-review']}
    row.save(update_fields=['value'])


class Migration(migrations.Migration):
    dependencies = [('core', '0006_seed_reserved_profile_paths')]
    operations = [migrations.RunPython(reserve_review_route, migrations.RunPython.noop)]
