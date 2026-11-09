"""category taxonomy

사용자가 정한 카테고리 목록에 맞춘다. 이름을 바꾸고 새 카테고리를 더하는 변경이라
이 마이그레이션을 먼저 실행해도 지금 배포된 앱은 그대로 동작한다.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

RENAMES = [
    ("housing", "주거", "주거비"),
    ("education", "교육", "교육비"),
    ("medical", "의료", "의료·건강"),
    ("beauty", "미용", "뷰티·미용"),
    ("finance", "금융·세금", "세금·공과금"),
]
ADDED = [("apparel", "의류", 30), ("dues", "경조사·회비", 120), ("pet", "반려동물", 150)]
# 목록에 없는 도서, 구독료, 자동차, 여행은 지우지 않고 뒤에 둔다. 합치면 되돌릴 수 없기 때문이다.
NEW_ORDER = {
    "food": 10,
    "housing": 20,
    "living": 40,
    "shopping": 50,
    "entertainment": 60,
    "education": 70,
    "insurance": 80,
    "telecom": 90,
    "transport": 100,
    "medical": 110,
    "finance": 130,
    "beauty": 140,
    "travel": 152,
    "car": 154,
    "subscription": 156,
    "books": 158,
}
OLD_ORDER = {
    "food": 10,
    "shopping": 20,
    "living": 30,
    "books": 40,
    "entertainment": 50,
    "subscription": 60,
    "telecom": 70,
    "insurance": 80,
    "transport": 90,
    "car": 100,
    "housing": 110,
    "education": 120,
    "medical": 130,
    "beauty": 140,
    "travel": 150,
    "finance": 160,
}
# 뱅크샐러드 대분류 -> 새 카테고리. 패션/쇼핑은 지금까지 쇼핑으로 묶였다.
NEW_MAPS = [("반려동물", "pet"), ("경조/선물", "dues")]


def _set_order(connection, order: dict[str, int]) -> None:
    connection.execute(
        sa.text("UPDATE category SET sort_order = :sort_order WHERE key = :key"),
        [{"key": key, "sort_order": value} for key, value in order.items()],
    )


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("UPDATE category SET name = :new WHERE key = :key AND name = :old"),
        [{"key": key, "old": old, "new": new} for key, old, new in RENAMES],
    )
    connection.execute(
        sa.text(
            "INSERT INTO category (key, name, kind, sort_order, is_system) "
            "VALUES (:key, :name, 'expense', :sort_order, true) ON CONFLICT (key) DO NOTHING"
        ),
        [{"key": key, "name": name, "sort_order": order} for key, name, order in ADDED],
    )
    _set_order(connection, NEW_ORDER)

    connection.execute(
        sa.text(
            "UPDATE category_map SET category_id = (SELECT id FROM category WHERE key = 'apparel') "
            "WHERE source_type = '지출' AND source_major = '패션/쇼핑' AND source_minor IS NULL"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO category_map (source_type, source_major, source_minor, category_id) "
            "SELECT '지출', :major, NULL, id FROM category WHERE key = :key "
            "ON CONFLICT ON CONSTRAINT uq_category_map_source DO NOTHING"
        ),
        [{"major": major, "key": key} for major, key in NEW_MAPS],
    )

    # 다음 가져오기를 기다리지 않고 이미 쌓인 거래도 새 분류로 옮긴다. 사용자가 고른 값은 건드리지 않는다.
    connection.execute(
        sa.text(
            "UPDATE txn_split s SET category_id = (SELECT id FROM category WHERE key = 'apparel') "
            "FROM txn t WHERE s.txn_id = t.id AND t.source_type = '지출' AND t.source_major = '패션/쇼핑' "
            "AND s.category_source = 'map' "
            "AND s.category_id = (SELECT id FROM category WHERE key = 'shopping')"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE txn_split s SET category_id = (SELECT id FROM category WHERE key = :key), category_source = 'map' "
            "FROM txn t WHERE s.txn_id = t.id AND t.source_type = '지출' AND t.source_major = :major "
            "AND s.category_source = 'auto' "
            "AND s.category_id = (SELECT id FROM category WHERE key = 'uncategorized_expense')"
        ),
        [{"major": major, "key": key} for major, key in NEW_MAPS],
    )


def downgrade() -> None:
    connection = op.get_bind()
    moves = [("apparel", "shopping"), ("dues", "uncategorized_expense"), ("pet", "uncategorized_expense")]
    for table, column in (("txn_split", "category_id"), ("rule", "set_category_id")):
        connection.execute(
            sa.text(
                f"UPDATE {table} SET {column} = (SELECT id FROM category WHERE key = :target) "
                f"WHERE {column} = (SELECT id FROM category WHERE key = :source)"
            ),
            [{"source": source, "target": target} for source, target in moves],
        )
    connection.execute(
        sa.text(
            "UPDATE category_map SET category_id = (SELECT id FROM category WHERE key = 'shopping') "
            "WHERE source_type = '지출' AND source_major = '패션/쇼핑' AND source_minor IS NULL"
        )
    )
    connection.execute(
        sa.text("DELETE FROM category_map WHERE source_type = '지출' AND source_major = :major"),
        [{"major": major} for major, _ in NEW_MAPS],
    )
    connection.execute(sa.text("DELETE FROM category WHERE key IN ('apparel', 'dues', 'pet')"))
    _set_order(connection, OLD_ORDER)
    connection.execute(
        sa.text("UPDATE category SET name = :old WHERE key = :key AND name = :new"),
        [{"key": key, "old": old, "new": new} for key, old, new in RENAMES],
    )
