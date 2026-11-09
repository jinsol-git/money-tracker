"""manual assets and rule direction

추가만 하는 변경이다. 이 마이그레이션을 먼저 실행해도 지금 배포된 앱은 그대로 동작한다.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('manual_asset',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('tenure', sa.Text(), nullable=True),
    sa.Column('car_class', sa.Text(), nullable=True),
    sa.Column('car_origin', sa.Text(), nullable=True),
    sa.Column('first_registered', sa.Date(), nullable=True),
    sa.Column('base_price', sa.BigInteger(), nullable=True),
    sa.Column('base_date', sa.Date(), nullable=True),
    sa.Column('acquired_on', sa.Date(), nullable=False),
    sa.Column('sold_on', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('car', 'real_estate', 'other')", name='ck_manual_asset_kind'),
    sa.CheckConstraint("tenure IS NULL OR tenure IN ('own', 'jeonse', 'wolse')", name='ck_manual_asset_tenure'),
    sa.CheckConstraint(
        "car_class IS NULL OR car_class IN ('small', 'compact', 'mid', 'large')", name='ck_manual_asset_car_class'
    ),
    sa.CheckConstraint(
        "car_origin IS NULL OR car_origin IN ('domestic', 'imported')", name='ck_manual_asset_car_origin'
    ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('manual_asset_value',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('asset_id', sa.BigInteger(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('source', sa.Text(), server_default='user', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source IN ('user', 'depreciation')", name='ck_manual_asset_value_source'),
    sa.ForeignKeyConstraint(['asset_id'], ['manual_asset.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('asset_id', 'as_of', name='uq_manual_asset_value_day')
    )
    op.add_column('rule', sa.Column('match_direction', sa.Text(), nullable=True))
    op.create_check_constraint(
        'ck_rule_match_direction', 'rule', "match_direction IS NULL OR match_direction IN ('out', 'in')"
    )


def downgrade() -> None:
    op.drop_constraint('ck_rule_match_direction', 'rule', type_='check')
    op.drop_column('rule', 'match_direction')
    op.drop_table('manual_asset_value')
    op.drop_table('manual_asset')
