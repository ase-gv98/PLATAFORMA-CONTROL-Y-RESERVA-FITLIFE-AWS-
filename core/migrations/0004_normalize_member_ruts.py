from django.db import migrations


def normalize_ruts(apps, schema_editor):
    MemberProfile = apps.get_model('core', 'MemberProfile')
    seen = set()
    members = list(
        MemberProfile.objects.exclude(rut__isnull=True).exclude(rut='').order_by('pk')
    )
    duplicates = []
    for member in members:
        normalized = member.rut.replace('.', '').replace('-', '').replace(' ', '').upper()
        if normalized in seen:
            duplicates.append(member.pk)
        else:
            seen.add(normalized)
    if duplicates:
        MemberProfile.objects.filter(pk__in=duplicates).update(rut=None)

    seen = set()
    for member in members:
        normalized = member.rut.replace('.', '').replace('-', '').replace(' ', '').upper()
        if normalized in seen:
            continue
        else:
            member.rut = f'{normalized[:-1]}-{normalized[-1:]}'
            seen.add(normalized)
            MemberProfile.objects.filter(pk=member.pk).update(rut=member.rut)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0003_membership_estado_payment_fields'),
    ]

    operations = [
        migrations.RunPython(normalize_ruts, migrations.RunPython.noop),
    ]
