from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from crm_api.database import get_db
from crm_api.schemas import LeadCreate, LeadUpdate, LeadResponse, PaginatedResponse
from crm_api.crud import lead_crud

router = APIRouter(prefix="/leads", tags=["leads"])


@router.post("/", response_model=LeadResponse)
def create_lead(lead: LeadCreate, db: Session = Depends(get_db)):
    return lead_crud.create(db, lead)


@router.get("/", response_model=PaginatedResponse[LeadResponse])
def get_leads(
    skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)
):
    return lead_crud.get_paginated(db, skip=skip, limit=limit)


@router.get("/{lead_id}", response_model=LeadResponse)
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = lead_crud.get(db, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.put("/{lead_id}", response_model=LeadResponse)
def update_lead(lead_id: int, lead: LeadUpdate, db: Session = Depends(get_db)):
    return lead_crud.update(db, lead_id, lead)


@router.delete("/{lead_id}")
def delete_lead(lead_id: int, db: Session = Depends(get_db)):
    return lead_crud.delete(db, lead_id)
