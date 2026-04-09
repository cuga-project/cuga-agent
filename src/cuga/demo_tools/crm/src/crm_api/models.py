from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from crm_api.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    industry = Column(String(100), nullable=True)
    website = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    # email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True)
    region = Column(String(50), nullable=True, index=True)
    annual_revenue = Column(Float, nullable=True)
    employee_count = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    contacts = relationship("Contact", back_populates="account")
    opportunities = relationship("Opportunity", back_populates="account")


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(50), nullable=True)
    company = Column(String(255), nullable=True)
    job_title = Column(String(100), nullable=True)
    industry = Column(String(100), nullable=True)
    source = Column(String(100), nullable=True)
    status = Column(String(50), nullable=True, default="new")
    score = Column(Integer, nullable=True, default=0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(50), nullable=True)
    job_title = Column(String(100), nullable=True)
    department = Column(String(100), nullable=True)
    is_primary = Column(Boolean, default=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    account = relationship("Account", back_populates="contacts")


class Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    value = Column(Float, nullable=False)
    currency = Column(String(3), default="USD")
    stage = Column(String(50), nullable=True, default="prospecting")
    probability = Column(Float, nullable=True, default=0.0)  # 0.0 to 1.0
    close_date = Column(DateTime(timezone=True), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    account = relationship("Account", back_populates="opportunities")
