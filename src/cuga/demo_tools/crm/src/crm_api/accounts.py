from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from crm_api.database import get_db
from crm_api.schemas import AccountCreate, AccountUpdate, AccountResponse, PaginatedResponse
from crm_api.crud import account_crud

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/", response_model=AccountResponse)
def create_account(account: AccountCreate, db: Session = Depends(get_db)):
    return account_crud.create(db, account)


@router.get("/", response_model=PaginatedResponse[AccountResponse])
def get_accounts(
    skip: int = Query(0, ge=0),
    limit: int = Query(300, ge=1, le=300),
    state: Optional[str] = Query(None, description="Filter accounts by state"),
    db: Session = Depends(get_db),
):
    return account_crud.get_paginated(db, skip=skip, limit=limit, state=state)


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    account = account_crud.get(db, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.put("/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, account: AccountUpdate, db: Session = Depends(get_db)):
    return account_crud.update(db, account_id, account)


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    return account_crud.delete(db, account_id)
