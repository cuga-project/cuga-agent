from sqlalchemy.orm import Session
import math
from typing import Optional

from crm_api.models import Account, Lead, Contact, Opportunity
from crm_api.schemas import (
    PaginatedResponse,
)


class BaseCRUD:
    def __init__(self, model):
        self.model = model

    def create(self, db: Session, obj_in):
        db_obj = self.model(**obj_in.dict())
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def get(self, db: Session, id: int):
        return db.query(self.model).filter(self.model.id == id).first()

    def get_paginated(self, db: Session, skip: int = 0, limit: int = 300):
        total = db.query(self.model).count()
        items = db.query(self.model).offset(skip).limit(limit).all()
        pages = math.ceil(total / limit) if limit > 0 else 0
        page = (skip // limit) + 1 if limit > 0 else 1

        return PaginatedResponse(items=items, total=total, page=page, pages=pages, per_page=limit)

    def update(self, db: Session, id: int, obj_in):
        db_obj = db.query(self.model).filter(self.model.id == id).first()
        if not db_obj:
            return None

        update_data = obj_in.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_obj, field, value)

        db.commit()
        db.refresh(db_obj)
        return db_obj

    def delete(self, db: Session, id: int):
        db_obj = db.query(self.model).filter(self.model.id == id).first()
        if not db_obj:
            return None

        db.delete(db_obj)
        db.commit()
        return {"message": f"{self.model.__name__} deleted successfully"}

    def count(self, db: Session):
        return db.query(self.model).count()


class AccountCRUD(BaseCRUD):
    def __init__(self):
        super().__init__(Account)

    def get_paginated(self, db: Session, skip: int = 0, limit: int = 300, state: Optional[str] = None):
        query = db.query(self.model)

        if state is not None:
            query = query.filter(self.model.state == state)

        total = query.count()
        items = query.offset(skip).limit(limit).all()
        pages = math.ceil(total / limit) if limit > 0 else 0
        page = (skip // limit) + 1 if limit > 0 else 1

        return PaginatedResponse(items=items, total=total, page=page, pages=pages, per_page=limit)


class LeadCRUD(BaseCRUD):
    def __init__(self):
        super().__init__(Lead)


class ContactCRUD(BaseCRUD):
    def __init__(self):
        super().__init__(Contact)

    def get_paginated(self, db: Session, skip: int = 0, limit: int = 300, email: Optional[str] = None):
        query = db.query(self.model)

        if email is not None:
            query = query.filter(self.model.email == email)

        total = query.count()
        items = query.offset(skip).limit(limit).all()
        pages = math.ceil(total / limit) if limit > 0 else 0
        page = (skip // limit) + 1 if limit > 0 else 1

        return PaginatedResponse(items=items, total=total, page=page, pages=pages, per_page=limit)


class OpportunityCRUD(BaseCRUD):
    def __init__(self):
        super().__init__(Opportunity)

    def get_paginated(self, db: Session, skip: int = 0, limit: int = 300, account_id: Optional[int] = None):
        query = db.query(self.model)

        if account_id is not None:
            query = query.filter(self.model.account_id == account_id)

        total = query.count()
        items = query.offset(skip).limit(limit).all()
        pages = math.ceil(total / limit) if limit > 0 else 0
        page = (skip // limit) + 1 if limit > 0 else 1

        return PaginatedResponse(items=items, total=total, page=page, pages=pages, per_page=limit)


# Create instances
account_crud = AccountCRUD()
lead_crud = LeadCRUD()
contact_crud = ContactCRUD()
opportunity_crud = OpportunityCRUD()
