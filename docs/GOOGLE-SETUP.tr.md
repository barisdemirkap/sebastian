# Google kurulumu (sadece sen yapabilirsin, yaklaşık 20 dakika)

Bu işlem Google hesabını gerektirir; hiçbir ajan bunu senin yerine yapmaz. Google arayüzü zaman zaman değişir. Bir yazı biraz farklıysa yakınındaki aynı kelimeyi ara ("Google Auth Platform" eskiden "OAuth izin ekranı" idi). Telefondan okuyabilirsin; Google Cloud adımlarını telefonda da yapabilirsin, terminal adımları için Mac gerekir.

Sonunda elinde olacaklar: sana ait özel bir Google Cloud projesi, "Sebastian" adında bir OAuth istemcisi ve Mac'inde bir anahtar dosyası. Sebastian takvimini okuyabilir; sen "evet" dedikten sonra etkinlik ekler, değiştirir, siler. Gmail şimdilik KULLANILMAZ (sonra aynı projeye eklenebilir, ayrı karar gerekir).

## 1. Google Cloud projesi oluştur
1. https://console.cloud.google.com/ adresini aç, kendi Google hesabınla giriş yap.
2. Üst çubukta proje seçiciye (Google Cloud logosunun yanı) dokun, **Yeni proje** (New project) seç.
3. Proje adı: `sebastian-personal`. Diğer alanlar olduğu gibi kalsın. **Oluştur** (Create), sonra seçiciden yeni projeyi seç.

## 2. Takvim API'sini aç
1. Menü (üç çizgi) > **API'ler ve Hizmetler** > **Kitaplık** (Library).
2. `Google Calendar API` ara, aç, **Etkinleştir** (Enable) düğmesine bas.
3. (Gmail: şimdi atla. İleride `Gmail API` aranıp etkinleştirilir; yeni izin ve ayrı karar gerekir.)

## 3. OAuth izin ekranını ayarla
1. Menü > **API'ler ve Hizmetler** > **OAuth izin ekranı** (yeni arayüzde **Google Auth Platform**, **Başlayın / Get started**).
2. Uygulama adı: `Sebastian`. Kullanıcı destek e-postası: kendi adresin. **İleri**.
3. Hedef kitle (Audience): **Harici (External)**. **İleri**.
4. İletişim bilgisi: kendi e-postan. **İleri**, politika kutusunu işaretle, **Devam**, sonra **Oluştur**.
5. Sol menüde **Hedef kitle / Audience**: yayın durumu **Test (Testing)** olmalı.
6. **Test kullanıcıları** altında **Kullanıcı ekle** (Add users) de, **kendi Google hesabının adresini** yaz (takvimini Sebastian'in yöneteceği hesap), **Kaydet**. Sadece listedeki kişiler giriş yapabilir.
7. Sol menüde **Veri erişimi / Data Access** (eski arayüzde "Kapsamlar" adımı) > **Kapsam ekle veya kaldır**. Şu ikisini seç (aramaya "calendar" yaz ya da "Kapsamları manuel ekle" kutusuna yapıştır):
   - `https://www.googleapis.com/auth/calendar.readonly`
   - `https://www.googleapis.com/auth/calendar.events`
   **Güncelle**, sonra **Kaydet**.

## 4. OAuth istemcisini oluştur (Masaüstü uygulaması)
1. Sol menüde **İstemciler / Clients** (eski arayüzde **Kimlik bilgileri** > **Kimlik bilgisi oluştur** > **OAuth istemci kimliği**) > **İstemci oluştur**.
2. Uygulama türü: **Masaüstü uygulaması (Desktop app)**. Ad: `Sebastian desktop`. **Oluştur**.
3. Açılan pencerede **İstemci kimliği** (`.apps.googleusercontent.com` ile biter) ve **İstemci gizli anahtarı** görünür. Pencereyi açık tut ya da **JSON'u indir** ile özel bir yere kaydet. Gizli anahtar sonra istemci sayfasında da görünür.

## 5. Kimlik ve gizli anahtarı Mac'ine koy
Bunları sohbete, git'e veya depo içindeki herhangi bir dosyaya ASLA yapıştırma.
```
mkdir -p ~/.config/sebastian/secrets && chmod 700 ~/.config/sebastian/secrets
read -s S && printf '%s' "$S" > ~/.config/sebastian/secrets/google.client_secret && unset S
chmod 600 ~/.config/sebastian/secrets/google.client_secret
```
(`read -s` gizli anahtarı yapıştırıp Enter'a basmanı bekler; ekranda görünmez.)

Ayar dosyası (`~/.config/sebastian/config.toml`, `config.example.toml` dosyasından kopyala): adresini yaz, 7. adıma kadar `enabled = false` kalsın, istemci kimliğini (gizli anahtarı DEĞİL) buraya yaz:
```
[[accounts]]
provider = "google"
address = "sen@gmail.com"
label = "personal"
scopes = ["https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/calendar.events"]
enabled = false

[oauth.google]
client_id = "123456789-abc.apps.googleusercontent.com"
```

## 6. İzin ver (OAuth)
1. Depoda: `.venv/bin/sebastian auth-url personal`. Uzun bir adres yazdırır (kimseyle bağlantı kurmaz).
2. Adresi, test kullanıcısı olarak giriş yaptığın bir tarayıcıda aç. (Telefonda açabilirsin.)
3. Google "Google bu uygulamayı doğrulamadı" uyarısı gösterebilir: kendi özel projen için normaldir, aşağıdaki "Doğrulanmamış uygulama uyarısı" bölümüne bak.
4. İzin ekranında Sebastian şunları isteyecek: "Google Takvim'de erişebildiğiniz tüm takvimleri görme ve indirme" (`calendar.readonly`) ve "Tüm takvimlerinizdeki etkinlikleri görüntüleme ve düzenleme" (`calendar.events`). Kutuları işaretle, **Devam**.
5. Tarayıcı `http://127.0.0.1:8765/callback?state=...&code=...` ile başlayan bir adrese gider ve "Siteye ulaşılamıyor" der. Bu beklenen durumdur, orada dinleyen bir şey yok. **Adres çubuğundaki adresin tamamını kopyala.** (Telefondaysan adresi Notlar'a yapıştırıp Mac'e gönder.)
6. Terminalde (bu komut Google'a bağlanır, bu yüzden canlı izin bayrağı gerekir):
   ```
   SEBASTIAN_ALLOW_LIVE=1 .venv/bin/sebastian auth-finish personal 'ADRESIN_TAMAMI_BURAYA'
   ```
   (Tırnakları bırak: adreste `&` var.) Anahtar `~/.config/sebastian/secrets/personal.token` dosyasına (0600) kaydedilir.

## 7. Aç
1. `config.toml` içinde hesap için `enabled = true` yap.
2. Sadece okuma testi: `SEBASTIAN_ALLOW_LIVE=1 .venv/bin/sebastian digest morning` bugünün özetini yazdırır.
3. Yazma ve sohbet için `docs/deploy.md` içindeki env satırları gerekir (`SEBASTIAN_ALLOW_LIVE=1`, `SEBASTIAN_ALLOW_WRITE=1`, `SEBASTIAN_OWNER_CHAT_ID`, `SEBASTIAN_NL=claude`, `SEBASTIAN_NL_CONSENT=1`). Sebastian her değişiklikten önce yine de "evet" bekler.

## Anahtarın süresi (önemli)
Uygulama **Test** durumundayken Google bu izinler için yenileme anahtarını 7 gün sonra geçersiz kılar. O zaman 6. adımı (`auth-url`, izin, `auth-finish`) tekrarlayana kadar Sebastian takvim hatası bildirir. İki seçenek:
- Test'te kal, 6. adımı haftada bir tekrarla (başka bir şey değişmez).
- Hedef kitle > **Uygulamayı yayınla (Publish app)** (durum "Üretimde", yine doğrulanmamış, sadece sen kullanıyorsun): anahtarlar 7 günde bitmez, doğrulanmamış uyarısını bir kez geçersin. Karar senin; kişisel kullanım için Google doğrulaması gerekmez.

## Doğrulanmamış uygulama uyarısı
"Google bu uygulamayı doğrulamadı" ya da "Bu uygulama doğrulanmadı" olarak görünür. Sebastian'i Google incelemesine göndermediğin için çıkar; sadece senin kullandığın uygulama için gerekmez. Şu üç şey doğruysa devam et: uygulama adı `Sebastian`, proje senin, adres çubuğunda `accounts.google.com` yazıyor.
1. **Gelişmiş (Advanced)** bağlantısına dokun (sol altta, küçük).
2. **Sebastian'e git (güvenli değil)** / **Go to Sebastian (unsafe)** seç.
3. Kutuları işaretleyip devam et. "Erişim engellendi" veya "Hata 403: access_denied" görürsen hesabın Test kullanıcıları listesinde değil (3.6): ekle ve tekrar dene.

## İzni geri alma
- Google hesabın: https://myaccount.google.com/permissions > **Sebastian** > **Tüm bağlantıları sil**. Sebastian'in anahtarı hemen çalışmaz olur.
- Mac'te: `~/.config/sebastian/secrets/personal.token` dosyasını sil (kullanmayacaksan `google.client_secret` dosyasını da).
- Cloud konsolu: **API'ler ve Hizmetler > Kimlik bilgileri**'nde `Sebastian desktop` istemcisini sil ya da `sebastian-personal` projesinin tamamını kapat (Menü > IAM ve Yönetici > Ayarlar > Kapat).
- Sebastian tarafı: `config.toml` içinde `enabled = false` yap; ya da `SEBASTIAN_ALLOW_WRITE=1` satırını kaldır: okumaya devam eder, hiçbir şey yazmaz.
