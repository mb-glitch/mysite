from django.contrib import admin


class ArchiveFilter(admin.SimpleListFilter):
    title = 'Archiwum'
    parameter_name = 'archive'
    field_path = 'is_archived'

    def lookups(self, request, model_admin):
        return (('archived', 'Archiwalne'), ('all', 'Wszystkie'))

    def queryset(self, request, queryset):
        if self.value() == 'archived':
            return queryset.filter(**{self.field_path: True})
        if self.value() == 'all':
            return queryset
        return queryset.filter(**{self.field_path: False})

    def choices(self, changelist):
        yield {
            'selected': self.value() is None,
            'query_string': changelist.get_query_string(remove=[self.parameter_name]),
            'display': 'Bieżące',
        }
        for value, label in self.lookup_choices:
            yield {
                'selected': self.value() == str(value),
                'query_string': changelist.get_query_string({self.parameter_name: value}),
                'display': label,
            }
