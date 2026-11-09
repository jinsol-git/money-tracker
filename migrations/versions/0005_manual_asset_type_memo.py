"""manual asset type and memo

추가만 하는 변경이다. 이 마이그레이션을 먼저 실행해도 지금 배포된 앱은 그대로 동작한다.

Revision ID: 0005
Revises: 0004
"""

from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('manual_asset', sa.Column('other_type', sa.Text(), nullable=True))
    op.add_column('manual_asset', sa.Column('memo', sa.Text(), nullable=True))
    op.create_check_constraint(
        'ck_manual_asset_other_type',
        'manual_asset',
        "other_type IS NULL OR other_type IN ('cash', 'physical', 'coin', 'fund', 'stock', 'pension')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_manual_asset_other_type', 'manual_asset', type_='check')
    op.drop_column('manual_asset', 'memo')
    op.drop_column('manual_asset', 'other_type')
