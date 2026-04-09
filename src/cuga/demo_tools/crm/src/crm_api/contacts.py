from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from crm_api.database import get_db
from crm_api.schemas import ContactCreate, ContactUpdate, ContactResponse, PaginatedResponse
from crm_api.crud import contact_crud

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.post("/", response_model=ContactResponse)
def create_contact(contact: ContactCreate, db: Session = Depends(get_db)):
    return contact_crud.create(db, contact)


@router.get("/", response_model=PaginatedResponse[ContactResponse])
def get_contacts(
    skip: int = Query(0, ge=0),
    limit: int = Query(300, ge=1, le=300),
    email: Optional[str] = Query(None, description="Filter contacts by email"),
    db: Session = Depends(get_db),
):
    return contact_crud.get_paginated(db, skip=skip, limit=limit, email=email)


@router.get("/{contact_id}", response_model=ContactResponse)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    contact = contact_crud.get(db, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.put("/{contact_id}", response_model=ContactResponse)
def update_contact(contact_id: int, contact: ContactUpdate, db: Session = Depends(get_db)):
    return contact_crud.update(db, contact_id, contact)


@router.delete("/{contact_id}")
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    return contact_crud.delete(db, contact_id)
