from __future__ import annotations

from backend.products.catalog import ProductSnapshot
from backend.products.repository import catalog_speech_terms
from backend.services.catalog_transcription import correct_catalog_words


class BrandAnchoredCatalog:
    source_name = "test_catalog"

    def __init__(self) -> None:
        self.products = [
            ProductSnapshot(
                product_id="oishi-snack",
                name="Oishi Snack Bắp Vị Sữa 34gr",
                brand="OISHI",
                category="Bánh Snack",
                category_hierarchy="Thực phẩm > Bánh kẹo > Snack",
                price_vnd=9000,
                stock=10,
            ),
            ProductSnapshot(
                product_id="oishi-snack-2",
                name="Snack bắp ngọt Oishi gói 42g",
                brand="OISHI",
                category="Bánh Snack",
                category_hierarchy="Thực phẩm > Bánh kẹo > Snack",
                price_vnd=9000,
                stock=10,
            ),
            ProductSnapshot(
                product_id="oishi-snack-3",
                name="Snack hành Oishi gói 39g",
                brand="OISHI",
                category="Bánh Snack",
                category_hierarchy="Thực phẩm > Bánh kẹo > Snack",
                price_vnd=9000,
                stock=10,
            ),
            ProductSnapshot(
                product_id="coca-zero",
                name="Nước giải khát Coca-Cola Zero chai 600ml",
                brand="COCA-COLA",
                category="Nước ngọt",
                category_hierarchy="Thực phẩm > Đồ uống > Nước ngọt",
                price_vnd=13000,
                stock=10,
            ),
            ProductSnapshot(
                product_id="coca-zero-2",
                name="Nước giải khát Coca-Cola Zero chai nhựa 1,5lít",
                brand="COCA-COLA",
                category="Nước ngọt",
                category_hierarchy="Thực phẩm > Đồ uống > Nước ngọt",
                price_vnd=13000,
                stock=10,
            ),
            ProductSnapshot(
                product_id="coca-zero-3",
                name="Nước giải khát Coca-Cola Zero 330ml",
                brand="COCA-COLA",
                category="Nước ngọt",
                category_hierarchy="Thực phẩm > Đồ uống > Nước ngọt",
                price_vnd=13000,
                stock=10,
            ),
        ]

    def search(self, query: str = "", limit: int = 10) -> list[ProductSnapshot]:
        normalized = query.casefold().replace("-", " ")
        return [
            product
            for product in self.products
            if normalized in product.brand.casefold().replace("-", " ")
            or normalized in product.name.casefold().replace("-", " ")
        ][:limit]

    def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
        return ("OISHI", "COCA-COLA")[:limit]


def test_corrects_catalog_word_near_miss_using_exact_brand_anchor() -> None:
    corrected, changes = correct_catalog_words(
        "Nắc Oishi vị cay còn hàng không?", BrandAnchoredCatalog()
    )

    assert corrected == "Snack Oishi vị cay còn hàng không?"
    assert [(change.source, change.replacement) for change in changes] == [("Nắc", "Snack")]


def test_corrects_phonetic_brand_miss_only_in_a_retail_question() -> None:
    corrected, changes = correct_catalog_words(
        "Nắc oi xi vị cay còn hàng không?", BrandAnchoredCatalog()
    )

    assert corrected == "Snack Oishi vị cay còn hàng không?"
    assert [(change.source, change.replacement) for change in changes] == [
        ("oi xi", "Oishi"),
        ("Nắc", "Snack"),
    ]


def test_does_not_fuzzy_correct_a_brand_in_unanchored_small_talk() -> None:
    text = "Mình nghe nói oi xi khá ngon."

    corrected, changes = correct_catalog_words(text, BrandAnchoredCatalog())

    assert corrected == text
    assert changes == []


def test_category_context_resolves_an_ambiguous_fuzzy_brand_from_the_catalog() -> None:
    class AmbiguousSnackCatalog(BrandAnchoredCatalog):
        def __init__(self) -> None:
            super().__init__()
            self.kiwi = ProductSnapshot(
                product_id="kiwi-snack",
                name="Snack vị trái cây Kiwi",
                brand="KIWI",
                category="Snack",
                category_hierarchy="Thực phẩm > Bánh kẹo > Snack",
                price_vnd=9000,
                stock=10,
            )

        def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
            return ("KIWI", "OISHI")[:limit]

        def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
            return (("Thực phẩm", "Bánh kẹo", "Snack"),)

        def search(self, query: str = "", category: str | None = None, limit: int = 10):
            if category:
                return [product for product in self.products if product.brand == "OISHI"]
            if query.casefold() == "oishi":
                return [product for product in self.products if product.brand == "OISHI"]
            if query.casefold() == "kiwi":
                return [self.kiwi]
            return super().search(query=query, limit=limit)

    corrected, changes = correct_catalog_words(
        "Nắc oi xi vị cay còn hàng không?", AmbiguousSnackCatalog()
    )

    assert corrected == "Snack Oishi vị cay còn hàng không?"
    assert [(change.source, change.replacement) for change in changes] == [
        ("oi xi", "Oishi"),
        ("Nắc", "Snack"),
    ]


def test_corrects_split_short_brand_only_with_exact_category_and_unique_catalog_match() -> None:
    class OralCareCatalog:
        def __init__(self, brands: tuple[str, ...]) -> None:
            self.products = [
                ProductSnapshot(
                    product_id=f"toothpaste-{brand}",
                    name=f"Kem đánh răng {brand}",
                    brand=brand,
                    category="Kem đánh răng",
                    category_hierarchy="Chăm sóc răng miệng > Kem đánh răng",
                    price_vnd=10000,
                    stock=5,
                )
                for brand in brands
            ]

        def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
            return tuple(product.brand for product in self.products if product.brand)[:limit]

        def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
            return (("Chăm sóc răng miệng", "Kem đánh răng"),)

        def search(
            self,
            query: str = "",
            category: str | None = None,
            limit: int = 10,
        ) -> list[ProductSnapshot]:
            if category:
                return [
                    product for product in self.products if product.category == category
                ][:limit]
            return []

    catalog = OralCareCatalog(("P/S", "COLGATE", "CLOSE UP", "DARLIE"))
    for text in (
        "Có kem đánh răng P, F cho răng nhạy cảm không?",
        "Có kem đánh răng P, S cho răng nhạy cảm không?",
        "Có kem đánh răng P, ép cho răng nhạy cảm không?",
    ):
        corrected, changes = correct_catalog_words(text, catalog)

        assert corrected == "Có kem đánh răng P/S cho răng nhạy cảm không?"
        assert len(changes) == 1
        assert changes[0].replacement == "P/S"


def test_does_not_guess_split_short_brand_when_catalog_candidates_tie() -> None:
    class AmbiguousAcronymCatalog:
        products = [
            ProductSnapshot(
                product_id=f"product-{brand}",
                name=f"Kem đánh răng {brand}",
                brand=brand,
                category="Kem đánh răng",
                category_hierarchy="Chăm sóc răng miệng > Kem đánh răng",
                price_vnd=10000,
                stock=5,
            )
            for brand in ("P/S", "P/F")
        ]

        def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
            return ("P/S", "P/F")[:limit]

        def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
            return (("Chăm sóc răng miệng", "Kem đánh răng"),)

        def search(
            self,
            query: str = "",
            category: str | None = None,
            limit: int = 10,
        ) -> list[ProductSnapshot]:
            if category:
                return [
                    product for product in self.products if product.category == category
                ][:limit]
            return []

    text = "Có kem đánh răng P, T không?"
    corrected, changes = correct_catalog_words(text, AmbiguousAcronymCatalog())

    assert corrected == text
    assert changes == []


def test_fuzzy_brand_is_restricted_by_an_explicit_product_category() -> None:
    class DiaperCatalog:
        source_name = "test_diaper_catalog"

        def __init__(self) -> None:
            self.product = ProductSnapshot(
                product_id="huggies-m",
                name="Tã quần Huggies size M",
                brand="HUGGIES",
                category="Tã quần",
                category_hierarchy="Chăm sóc bé > Tã quần",
                price_vnd=100000,
                stock=10,
            )
            self.products = [
                self.product,
                ProductSnapshot(
                    product_id="bobby-m",
                    name="Tã quần Bobby size M",
                    brand="BOBBY",
                    category="Tã quần",
                    category_hierarchy="Chăm sóc bé > Tã quần",
                    price_vnd=95000,
                    stock=10,
                ),
                ProductSnapshot(
                    product_id="sunmate-m",
                    name="Tã quần Sunmate size M",
                    brand="SUNMATE",
                    category="Tã quần",
                    category_hierarchy="Chăm sóc bé > Tã quần",
                    price_vnd=90000,
                    stock=10,
                ),
            ]

        def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
            return ("HUGGIES", "TÂN TÂN")

        def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
            return (("Chăm sóc bé", "Tã quần"),)

        def search(self, query: str = "", category: str | None = None, limit: int = 10):
            if category:
                return self.products[:limit]
            if "huggies" in query.casefold():
                return [self.product]
            return []

    corrected, changes = correct_catalog_words(
        "Tả quần hoogie cỡ M còn hàng không?", DiaperCatalog()
    )

    assert corrected == "Tã quần Huggies cỡ M còn hàng không?"
    assert [(change.source, change.replacement) for change in changes] == [
        ("hoogie", "Huggies"),
        ("Tả", "Tã"),
    ]

    corrected, changes = correct_catalog_words(
        "Tả quần hưu ghi cỡ M còn hàng không?", DiaperCatalog()
    )

    assert corrected == "Tã quần Huggies cỡ M còn hàng không?"
    assert [(change.source, change.replacement) for change in changes] == [
        ("hưu ghi", "Huggies"),
        ("Tả", "Tã"),
    ]


def test_does_not_replace_an_unknown_shampoo_name_with_a_food_brand() -> None:
    class ShampooCatalog:
        def __init__(self) -> None:
            self.product = ProductSnapshot(
                product_id="pantene-shampoo",
                name="Dầu gội Pantene 300ml",
                brand="PANTENE",
                category="Dầu gội nữ",
                category_hierarchy="Hoá mỹ phẩm > Chăm sóc tóc > Dầu gội nữ",
                price_vnd=100000,
                stock=10,
            )

        def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
            return ("CUNG ĐÌNH", "PANTENE")

        def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
            return (("Hoá mỹ phẩm", "Chăm sóc tóc", "Dầu gội nữ"),)

        def search(self, query: str = "", category: str | None = None, limit: int = 10):
            if category:
                return [self.product]
            if "pantene" in query.casefold():
                return [self.product]
            return []

    text = "Dầu gội sung sinh đang có khuyến mãi không?"

    corrected, changes = correct_catalog_words(text, ShampooCatalog())

    assert corrected == text
    assert changes == []


def test_corrects_misspelled_product_variant_from_the_catalog() -> None:
    corrected, changes = correct_catalog_words(
        "Cho mình hỏi Coca Gero giá bao nhiêu?", BrandAnchoredCatalog()
    )

    assert corrected == "Cho mình hỏi Coca Zero giá bao nhiêu?"
    assert [(change.source, change.replacement) for change in changes] == [("Gero", "Zero")]


def test_corrects_misheard_packaging_word_only_before_a_measured_size() -> None:
    corrected, changes = correct_catalog_words(
        "Coca Zero trai 600ml giá bao nhiêu?", BrandAnchoredCatalog()
    )

    assert corrected == "Coca Zero chai 600ml giá bao nhiêu?"
    assert [(change.source, change.replacement) for change in changes] == [("trai", "chai")]

    ordinary_text = "Bạn nam ấy là trai mới lớn."
    corrected, changes = correct_catalog_words(ordinary_text, BrandAnchoredCatalog())
    assert corrected == ordinary_text
    assert changes == []


def test_does_not_rewrite_unanchored_ordinary_conversation() -> None:
    text = "Cảm ơn các bạn đã theo dõi và hẹn gặp lại."

    corrected, changes = correct_catalog_words(text, BrandAnchoredCatalog())

    assert corrected == text
    assert changes == []


def test_does_not_treat_k_e_homophone_as_a_shelf_cue() -> None:
    class HomophoneCatalog:
        product = ProductSnapshot(
            product_id="benew-product",
            name="BENEW snack",
            brand="BENEW",
            category="Snack",
            price_vnd=9000,
            stock=10,
        )

        def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
            return ("BENEW",)[:limit]

        def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
            return ()

        def search(self, query: str = "", category: str | None = None, limit: int = 10):
            return [self.product] if query.casefold() == "benew" else []

    text = "Mọi danh từ, kế bên từ SI dành cho bạn, luôn bắt đầu bằng chữ viết hoa."

    corrected, changes = correct_catalog_words(text, HomophoneCatalog())

    assert corrected == text
    assert changes == []


def test_does_not_treat_quoc_gia_as_a_price_cue() -> None:
    class HomophoneCatalog:
        product = ProductSnapshot(
            product_id="xuyen-a-product",
            name="Xuyên Á snack",
            brand="XUYEN A",
            category="Snack",
            price_vnd=9000,
            stock=10,
        )

        def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
            return ("XUYEN A",)[:limit]

        def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
            return ()

        def search(self, query: str = "", category: str | None = None, limit: int = 10):
            return [self.product] if query.casefold() == "xuyen a" else []

    text = "Một quốc gia tập trung quyền lực trong một thời kỳ dài."

    corrected, changes = correct_catalog_words(text, HomophoneCatalog())

    assert corrected == text
    assert changes == []


def test_does_not_rewrite_words_that_already_match_the_catalog() -> None:
    text = "Có Coca Zero không?"

    corrected, changes = correct_catalog_words(text, BrandAnchoredCatalog())

    assert corrected == text
    assert changes == []


def test_does_not_guess_when_no_catalog_brand_anchors_the_word() -> None:
    class UnanchoredCatalog:
        def search(self, query: str = "", limit: int = 10) -> list[ProductSnapshot]:
            return [BrandAnchoredCatalog().products[0]] if query == "Nắc" else []

    text = "Nắc gì đây?"
    corrected, changes = correct_catalog_words(text, UnanchoredCatalog())

    assert corrected == text
    assert changes == []


def test_does_not_apply_vietnamese_catalog_correction_to_other_languages() -> None:
    text = "Coca Gero price?"

    corrected, changes = correct_catalog_words(text, BrandAnchoredCatalog(), language="en")

    assert corrected == text
    assert changes == []


def test_speech_vocabulary_is_derived_from_catalog_brands_and_categories() -> None:
    terms = catalog_speech_terms(BrandAnchoredCatalog().products, limit=6)

    assert "OISHI" in terms
    assert "COCA-COLA" in terms
    assert "Snack" in terms
    assert "Nước ngọt" in terms
