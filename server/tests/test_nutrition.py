"""nutrition.py 里不联网、不开数据库的部分：数字解析、取整、搜索分词、Open Food Facts 商品 → 候选的换算。"""
from __future__ import annotations

import math

import pytest

import nutrition
from config import settings


@pytest.fixture(autouse=True)
def english(monkeypatch):
    monkeypatch.setattr(settings, "language", "en")


# —— num / r1 / complete ————————————————————————————————————————————————


@pytest.mark.parametrize("v,expected", [
    (5, 5.0), (2.5, 2.5), (0, 0.0), ("12.3", 12.3), (" 7 ", 7.0), ("1,234", 1234.0), ("-3", -3.0),
    ("Tr", 0.0), ("tr", 0.0), ("TRACE", 0.0),
])
def test_num_values(v, expected):
    assert nutrition.num(v) == expected


@pytest.mark.parametrize("v", [None, "", "  ", "N", "n", "abc", "12g", [], {}])
def test_num_missing(v):
    assert nutrition.num(v) is None


@pytest.mark.xfail(reason="逗号一律当千位分隔符去掉：欧洲写法的小数「1,5」读成 15.0（差 10 倍）", strict=True)
def test_num_decimal_comma():
    assert nutrition.num("1,5") == 1.5


@pytest.mark.xfail(reason="float() 接受「nan」「inf」：表格或接口里的这类字符串会被当成数参与计算，而不是当成「没有」", strict=True)
@pytest.mark.parametrize("v", ["nan", "inf"])
def test_num_rejects_non_finite(v):
    out = nutrition.num(v)
    assert out is None or math.isfinite(out)


@pytest.mark.parametrize("v,expected", [(None, None), (1.26, 1.3), (1.24, 1.2), (3, 3.0), (-0.04, -0.0)])
def test_r1(v, expected):
    assert nutrition.r1(v) == expected


def test_complete():
    full = {"kcal": 100, "protein": 1, "carb": 0, "fat": 0.0}
    assert nutrition.complete(full) is True
    assert nutrition.complete({**full, "fat": None}) is False
    assert nutrition.complete({"kcal": 100}) is False
    assert nutrition.complete({}) is False


# —— _words —————————————————————————————————————————————————————————————


@pytest.mark.parametrize("text,expected", [
    ("Berries, mixed", ["berry", "mixed"]),
    ("Glasses BOXES buzzes", ["glass", "box", "buzz"]),
    ("Oats", ["oat"]),
    ("Grass", ["grass"]),
    ("Milk 2% fat", ["milk", "2", "fat"]),
    ("", []),
    (None, []),
    ("ties", ["ty"]),
    ("as is", ["as", "is"]),  # 两个字母的词不还原
])
def test_words(text, expected):
    assert nutrition._words(text) == expected


@pytest.mark.xfail(reason="「-oes」复数只去掉 s：tomatoes → tomatoe，前缀对不上 CoFID 里的「Tomato…」，搜「tomatoes」找不到番茄", strict=True)
def test_words_oes_plural_matches_singular():
    q = nutrition._words("tomatoes")
    name = nutrition._words("Tomato puree")
    assert all(any(x.startswith(w) for x in name) for w in q)


# —— _first ————————————————————————————————————————————————————————————


@pytest.mark.parametrize("v,expected", [
    ("Brand A, Brand B", "Brand A"), (["X", "Y"], "X"), ([], None), ("", None), (None, None), ("  Solo ", "Solo"),
])
def test_first(v, expected):
    assert nutrition._first(v) == expected


# —— off_per100 / off_candidate —————————————————————————————————————————


def test_off_per100_kcal_direct():
    n = {"energy-kcal_100g": 250, "proteins_100g": "10.04", "carbohydrates_100g": 30, "fat_100g": 9.96,
         "sugars_100g": None, "fiber_100g": "Tr", "saturated-fat_100g": 1, "salt_100g": 0.5}
    assert nutrition.off_per100(n, "_100g") == {"kcal": 250.0, "protein": 10.0, "carb": 30.0, "fat": 10.0,
                                                "sugars": None, "fibre": 0.0, "satfat": 1.0, "salt": 0.5}


def test_off_per100_kj_converted():
    assert nutrition.off_per100({"energy_100g": 1046}, "_100g")["kcal"] == 250.0


def test_off_per100_empty():
    assert all(v is None for v in nutrition.off_per100({}, "_100g").values())


def product(**kw):
    p = {"code": "0000000000017", "product_name": "Test Bar", "brands": "Fictional Foods, Other",
         "nutriments": {"energy-kcal_100g": 400, "proteins_100g": 20, "carbohydrates_100g": 50, "fat_100g": 12}}
    p.update(kw)
    return p


def test_off_candidate_solid():
    c = nutrition.off_candidate(product(serving_quantity="40", serving_size="1 bar (40 g)", countries_tags=["en:united-kingdom"]))
    assert c["source"] == "off"
    assert c["ref"] == "0000000000017"
    assert c["name"] == "Test Bar"
    assert c["brand"] == "Fictional Foods"
    assert c["basis"] == "g"
    assert c["complete"] is True and c["prepared"] is False
    assert c["uk"] is True
    assert c["portions"] == [{"label": "1 bar (40 g)", "grams": 40.0}]
    assert c["url"] == "https://world.openfoodfacts.org/product/0000000000017"


def test_off_candidate_prefers_english_name():
    assert nutrition.off_candidate(product(product_name_en=" English Name "))["name"] == "English Name"


@pytest.mark.parametrize("kw", [
    {"nutrition_data_per": "100ml"},
    {"product_quantity_unit": "ml"},
    {"quantity": "330 ml"},
    {"quantity": "1.5L"},
    {"quantity": "75cl bottle"},
])
def test_off_candidate_liquid_basis(kw):
    assert nutrition.off_candidate(product(**kw))["basis"] == "ml"


def test_off_candidate_quantity_ignored_when_per_given():
    assert nutrition.off_candidate(product(nutrition_data_per="100g", quantity="500 ml"))["basis"] == "g"


def test_off_candidate_prepared_fallback():
    p = product(nutriments={"energy-kcal_prepared_100g": 60, "proteins_prepared_100g": 3})
    c = nutrition.off_candidate(p)
    assert c["prepared"] is True
    assert c["per100"]["kcal"] == 60.0
    assert c["complete"] is False


def test_off_candidate_no_nutrition_is_none():
    assert nutrition.off_candidate(product(nutriments={})) is None
    assert nutrition.off_candidate({}) is None


def test_off_candidate_default_serving_label_and_bad_serving():
    c = nutrition.off_candidate(product(serving_quantity=30))
    assert c["portions"] == [{"label": "1 serving", "grams": 30.0}]
    for sq in (0, -5, "N", None):
        assert nutrition.off_candidate(product(serving_quantity=sq))["portions"] == []


def test_off_candidate_without_code():
    c = nutrition.off_candidate(product(code=None))
    assert c["ref"] == "" and c["url"] is None
