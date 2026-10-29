from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import generate_api_key, hash_api_key, require_admin
from app.db import get_session
from app.models import Owner
from app.schemas import OwnerCreate, OwnerCreated

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/owners", status_code=status.HTTP_201_CREATED, response_model=OwnerCreated)
async def create_owner(body: OwnerCreate, session: AsyncSession = Depends(get_session)):
    api_key = generate_api_key()
    owner = Owner(name=body.name, api_key_hash=hash_api_key(api_key))
    session.add(owner)
    await session.commit()
    return OwnerCreated(id=owner.id, name=owner.name, api_key=api_key)
