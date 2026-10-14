# Aktualizacja APK z własnego serwera

Aplikacja sprawdza wersję po zalogowaniu, przy pierwszym udanym odczycie profilu
w danym uruchomieniu. Jeśli dostępny jest wyższy `versionCode`, nad dashboardem
pojawia się „Dostępna aktualizacja” i przycisk „Pobierz i zainstaluj”.
Brak wydania, brak internetu lub starszy backend nie blokują dashboardu.
Nie ma pobierania ani instalowania bez kliknięcia.

## Pierwsza instalacja i podpis

Wydanie z obsługą aktualizacji ma teraz `versionCode = 2`, `versionName = "1.1"`.
Zainstaluj je jednorazowo ręcznie. Wcześniejszy APK nie zawiera jeszcze funkcji
sprawdzania aktualizacji. Następne wydanie powinno mieć kod 3, kolejne 4 itd.,
niezależnie od opisowego `versionName`.

Android wymaga tej samej nazwy pakietu `com.macbilicki.domowydashboard` i tego
samego klucza podpisu co w zainstalowanej aplikacji. Prawidłowa aktualizacja
zachowuje logowanie i dane. Aplikacja odrzuci APK z innym pakietem, wersją,
podpisem, rozmiarem lub sumą SHA-256, zanim otworzy instalator.

Dla stałych wydań wybierz w Android Studio **Generate Signed Bundle / APK → APK**
i używaj jednego własnego keystore. Możesz zapisać go lokalnie w
`.secrets/android-release.jks` (katalog ignorowany przez Git). Zachowaj kopię
klucza i hasła; klucz podpisu nie jest potrzebny na serwerze Django.

APK debug można również aktualizować, jeśli wszystkie wydania mają ten sam
klucz debug. Release podpisany innym kluczem nie zaktualizuje istniejącej
instalacji debug; przejście wymaga osobnej, ręcznej instalacji, zwykle po
odinstalowaniu debug, co usuwa jego zapisane logowanie. Nie zmieniaj klucza
pomiędzy kolejnymi wydaniami udostępnianymi tym samym telefonom.

## Przygotowanie wydania lokalnie

1. Zwiększ `versionCode` w `android/app/build.gradle.kts` i ustaw `versionName`.
2. Zbuduj podpisany APK w Android Studio. Dla istniejących instalacji debug:

   ```sh
   cd android
   ./gradlew :app:assembleDebug
   cd ..
   ```

3. Z katalogu głównego projektu uruchom skrypt, podając rzeczywisty APK:

   ```sh
   python3 android/prepare_release.py android/app/build/outputs/apk/debug/app-debug.apk
   ```

   Dla podpisanego release podaj ścieżkę do jego pliku APK. Skrypt odczytuje
   `applicationId`, `versionCode` i `versionName` z APK narzędziem `aapt`,
   sprawdza podpis przez `apksigner` oraz wylicza SHA-256. SDK znajduje przez
   istniejące `android/local.properties` albo `ANDROID_HOME` / `ANDROID_SDK_ROOT`.
   Java i Android SDK Build Tools są wymagane tylko lokalnie do przygotowania
   wydania. Skrypt nie podpisuje APK i nie wysyła nic na serwer.

Powstaje katalog `android_releases/` w głównym katalogu projektu:

```text
android_releases/
  dashboard-<versionCode>-<pierwsze12znakówSHA256>.apk
  release.json
```

Katalog jest ignorowany przez Git. Skrypt przygotowuje metadane na podstawie
rzeczywistego APK, więc nie musisz ręcznie przepisywać wersji ani sumy.

## Publikacja na hostingu

Po wdrożeniu kodu Django i restarcie aplikacji utwórz katalog
`/home/host109829/mysite/android_releases/` i skopiuj do niego przygotowane pliki.
Nie potrzeba migracji ani dodatkowego wpisu w `.env`: Django używa katalogu
`android_releases` względem projektu, lokalnie i na produkcji.

**Najpierw prześlij APK, potem `release.json`.** Metadane najlepiej wysłać pod
nazwą tymczasową i na końcu przemianować na `release.json`, żeby aplikacja nie
odczytała częściowo przesłanego pliku. Nowe APK mają unikalne nazwy; nie
nadpisuj już opublikowanego APK inną zawartością. Zachowaj starsze APK przez
czas potrzebny telefonom na dokończenie rozpoczętych pobrań.

To ręczna publikacja plików na serwerze, wykonywana przez administratora.
Podczas przygotowania tej funkcji nie publikowano plików na hostingu.

## Instalacja na telefonie

Po wykryciu nowej wersji:

1. Kliknij „Pobierz i zainstaluj”.
2. Przy pierwszej aktualizacji aplikacja poprosi o otwarcie ustawień Androida.
   Zezwól Domowemu dashboardowi na instalowanie z tego źródła i wróć.
3. Aplikacja pobierze APK, pokaże postęp i sprawdzi jego zawartość.
4. Systemowy instalator poprosi o potwierdzenie aktualizacji.

Anulowanie pozostawia obecną aplikację. Obrót/zamknięcie ekranu podczas
pobierania przerywa pobieranie; po ponownym otwarciu można spróbować jeszcze
raz. Jeśli podczas zmiany ustawień Android odtworzy ekran, ponownie kliknij
przycisk aktualizacji. Instalacja wymaga wolnego miejsca i nie jest cicha.

Pobieranie korzysta z HTTPS i tokenu zalogowanego użytkownika. Adres jest
ograniczony do skonfigurowanego serwera; przekierowania nie są obsługiwane.
APK trafia do prywatnego cache aplikacji. FileProvider udostępnia instalatorowi
wyłącznie podkatalog `updates/`, nigdy tokeny logowania ani inne dane aplikacji.

## API v1

Oba endpointy wymagają tokenu DRF aktywnego użytkownika z dostępem do co najmniej
jednego dashboardu. Konta techniczne nie mają dostępu. `GET`, `HEAD`, `OPTIONS`
są dozwolone; zapis przez API nie jest możliwy. Odpowiedzi mają `Cache-Control:
no-store`.

`GET /api/v1/app/update/` bez opublikowanego wydania:

```json
{"available": false}
```

Przy wydaniu zwracane są `available`, `version_code`, `version_name`,
`package_name`, `apk`, `sha256`, `size` (bajty) i `download_path`.
`download_path` to ścieżka `/api/v1/app/apk/<nazwa-pliku>/` względem adresu
serwera zapisanego w aplikacji. Telefon sam porównuje `version_code` z obecną
instalacją. Błędne metadane/brak zgodnego pliku: 503, bez treści wyjątku.

`GET /api/v1/app/apk/<nazwa-pliku>/` strumieniuje APK, także zachowane starsze
wydania. Nazwy są ograniczone do formatu wygenerowanego przez skrypt; ścieżki
względne i symlinki poza katalog nie są obsługiwane. Limit pliku: 100 MiB.
Brak/błędny plik: 404, brak tokenu: 401, brak uprawnień: 403.

## Sprawdzenia

Testy Django sprawdzają uprawnienia, metadane, pobieranie, zachowanie starszych
APK i ochronę przed odczytem plików poza katalogiem. Testy Kotlin sprawdzają
porównanie wersji, adres pobierania oraz walidację rozmiaru i SHA-256.
Skrypt sprawdzono na rzeczywistym, lokalnie zbudowanym i podpisanym APK debug.
Nie potwierdzono jeszcze systemowego dialogu instalacji i aktualizacji danych
na telefonie; wymagają dwóch podpisanych tym samym kluczem wydań na urządzeniu.

Dokumentacja: [podpisywanie APK](https://developer.android.com/studio/publish/app-signing),
[zgoda na instalowanie](https://developer.android.com/reference/android/content/pm/PackageManager#canRequestPackageInstalls()),
[FileProvider](https://developer.android.com/reference/androidx/core/content/FileProvider).
