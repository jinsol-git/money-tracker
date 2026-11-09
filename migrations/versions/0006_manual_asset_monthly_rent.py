"""manual asset monthly rent

추가만 하는 변경이다. 이 마이그레이션을 먼저 실행해도 지금 배포된 앱은 그대로 동작한다.

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('manual_asset', sa.Column('monthly_rent', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('manual_asset', 'monthly_rent')
