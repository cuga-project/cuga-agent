from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from crm_api.database import get_db
from crm_api.schemas import OpportunityCreate, OpportunityUpdate, OpportunityResponse, PaginatedResponse
from crm_api.crud import opportunity_crud

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.post("/", response_model=OpportunityResponse)
def create_opportunity(opportunity: OpportunityCreate, db: Session = Depends(get_db)):
    return opportunity_crud.create(db, opportunity)


@router.get("/", response_model=PaginatedResponse[OpportunityResponse])
def get_opportunities(
    skip: int = Query(0, ge=0),
    limit: int = Query(300, ge=1, le=300),
    account_id: Optional[int] = Query(None, description="Filter opportunities by account ID"),
    db: Session = Depends(get_db),
):
    return opportunity_crud.get_paginated(db, skip=skip, limit=limit, account_id=account_id)


@router.get("/{opportunity_id}", response_model=OpportunityResponse)
def get_opportunity(opportunity_id: int, db: Session = Depends(get_db)):
    opportunity = opportunity_crud.get(db, opportunity_id)
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opportunity


@router.put("/{opportunity_id}", response_model=OpportunityResponse)
def update_opportunity(opportunity_id: int, opportunity: OpportunityUpdate, db: Session = Depends(get_db)):
    return opportunity_crud.update(db, opportunity_id, opportunity)


@router.delete("/{opportunity_id}")
def delete_opportunity(opportunity_id: int, db: Session = Depends(get_db)):
    return opportunity_crud.delete(db, opportunity_id)
