from django.contrib import admin
from .models import Dziecko, Wiadomosc, Ogloszenie

@admin.register(Dziecko)
class DzieckoAdmin(admin.ModelAdmin):
    list_display = ('user_first_name', 'user_last_name', 'librus_login')
    search_fields = ('user__first_name', 'user__last_name', 'librus_login')

    def user_first_name(self, obj):
        return obj.user.first_name
    user_first_name.short_description = 'Imię'

    def user_last_name(self, obj):
        return obj.user.last_name
    user_last_name.short_description = 'Nazwisko'
    
    
@admin.register(Wiadomosc)
class WiadomoscAdmin(admin.ModelAdmin):
    # Pola widoczne na liście
    list_display = ('temat', 'dziecko', 'sent_at', 'created', 'is_sent')
    
    # Filtry po prawej stronie
    list_filter = ('dziecko', 'sent_at', 'created')
    
    # Wyszukiwarka (możesz szukać po polach relacji używając __)
    search_fields = ('temat', 'nadawca', 'dziecko__imie', 'dziecko__nazwisko')
    
    # Pola tylko do odczytu (wymagane dla pól z auto_now_add=True)
    readonly_fields = ('created',)
    
    # Własna kolumna logiczna (ikona zamiast daty na liście)
    def is_sent(self, obj):
        return obj.sent_at is not None
    is_sent.boolean = True
    is_sent.short_description = "Wysłano e-mail?"



@admin.register(Ogloszenie)
class OgloszenieAdmin(admin.ModelAdmin):
    # Pola widoczne na liście
    list_display = ('tytul', 'nadawca', 'dziecko', 'sent_at', 'created', 'librus_data', 'is_sent')
    
    # Filtry po prawej stronie
    list_filter = ('dziecko', 'sent_at', 'created')
    
    # Wyszukiwarka (możesz szukać po polach relacji używając __)
    search_fields = ('tytul', 'nadawca', 'dziecko')
    
    # Pola tylko do odczytu (wymagane dla pól z auto_now_add=True)
    readonly_fields = ('created',)
    
    # Własna kolumna logiczna (ikona zamiast daty na liście)
    def is_sent(self, obj):
        return obj.sent_at is not None
    is_sent.boolean = True
    is_sent.short_description = "Wysłano e-mail?"
