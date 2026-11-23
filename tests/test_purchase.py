import pytest

from sebastian.purchase import classify


@pytest.mark.parametrize("text,items", [
    ("ekmek alinacak domates alinacak", ["Ekmek", "Domates"]),
    ("süt lazım", ["Süt"]),
    ("2 litre süt almamız lazım", ["2 litre süt"]),
    ("peynir ve zeytin bitti", ["Peynir", "Zeytin"]),
    ("kahve kalmadı", ["Kahve"]),
    ("makarna tükendi", ["Makarna"]),
    ("market: un, şeker, tuz", ["Un", "Şeker", "Tuz"]),
    ("listeye süt", ["Süt"]),
    ("çay'ı unutma", ["Çay"]),
    ("eve limon lazım", ["Limon"]),
    ("un al", ["Un"]),
    ("bulgur\nmercimek\nalınacak", ["Bulgur", "Mercimek"]),
    ("ekmek lazım ekmek alınacak", ["Ekmek"]),
])
def test_purchase_wordings(text, items):
    r = classify(text)
    assert r and r["items"] == items and not r["event_like"]


@pytest.mark.parametrize("text", [
    "yarın 15:00 doktor randevusu alınacak", "toplantı lazım", "vergi dairesine gitmem lazım", "sağlık raporu al",
    "ekmek al bence", "süt alınacak mı?", "randevu al", "ekmek ekle", "berberi aramak lazım",
])
def test_not_a_purchase(text):
    assert classify(text) is None


def test_day_or_time_next_to_a_purchase_is_event_like_and_not_an_item():
    r = classify("yarın ekmek alınacak")
    assert r == {"items": ["Ekmek"], "event_like": True, "ranout": False, "done_like": False}
    assert classify("salı günü saat 3 süt al")["items"] == ["Süt"]
    assert classify("çamaşır bitti")["done_like"] and classify("süt kalmadı")["done_like"] is False
