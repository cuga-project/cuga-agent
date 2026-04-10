# CRM System API

A comprehensive FastAPI-based CRM system with Accounts, Leads, Contacts, and Opportunities management.

## Features

- **Full CRUD operations** for all entities (Accounts, Leads, Contacts, Opportunities)
- **Pagination support** (20 records per page by default)
- **Advanced querying capabilities** for agent testing
- **Comprehensive test data** (hundreds of records)
- **RESTful API** with automatic documentation

## Quick Start

### Option 1: Install as Python Package

1. **Install the package:**
   ```bash
   uv sync
   ```

2. **Run the application:**
   ```bash
   uv run python -m crm_api.main
   ```

3. **Command line options:**
   ```bash
   python -m crm_api.main --help
   # Shows: --host, --port, --reload options
   ```

### Option 2: Development Setup

1. **Install dependencies:**
   ```bash
   uv sync
   ```

2. **Run the application:**
   ```bash
   uv run python main.py
   ```
   Or use the provided script:
   ```bash
   uv run python run.py
   ```

3. **Access the API:**
   - API Documentation: http://localhost:8005/docs
   - Alternative docs: http://localhost:8005/redoc

## Testing

### Running E2E Tests

The project uses **end-to-end (e2e) tests** that interact with a real database and FastAPI application, providing true integration testing for the query "Find all contacts from my CRM accounts for opportunities $10k and 50% likelihood".

Run the complete test suite:
```bash
python run_tests.py
```

Or run pytest directly:
```bash
uv sync
uv run python -m pytest -v tests/
```

### Test Coverage

The e2e tests cover:
- **Real Database Operations**: Tests use actual SQLite database with seeded test data
- **Full API Integration**: Tests make real HTTP requests to FastAPI endpoints
- **Business Logic**: Tests the complete CRM query with various filter combinations
- **Parameter Validation**: Tests query parameter constraints and error handling
- **Pagination**: Tests skip/limit functionality with real data
- **Data Consistency**: Verifies API responses match direct database queries

### Test Data

Tests use realistic sample data:
- **Accounts**: Test Corp (Tech, $50k revenue) and Another Corp (Finance, $20k revenue)
- **Opportunities**: Various values ($5k-$25k) and probabilities (30%-70%)
- **Contacts**: CEO, CTO, CFO with realistic details

## API Endpoints

### Standard CRUD Operations

#### Accounts
- `GET /accounts/` - List all accounts (paginated)
- `POST /accounts/` - Create new account
- `GET /accounts/{id}` - Get account by ID
- `PUT /accounts/{id}` - Update account
- `DELETE /accounts/{id}` - Delete account

#### Leads
- `GET /leads/` - List all leads (paginated)
- `POST /leads/` - Create new lead
- `GET /leads/{id}` - Get lead by ID
- `PUT /leads/{id}` - Update lead
- `DELETE /leads/{id}` - Delete lead

#### Contacts
- `GET /contacts/` - List all contacts (paginated)
- `POST /contacts/` - Create new contact
- `GET /contacts/{id}` - Get contact by ID
- `PUT /contacts/{id}` - Update contact
- `DELETE /contacts/{id}` - Delete contact

#### Opportunities
- `GET /opportunities/` - List all opportunities (paginated)
- `POST /opportunities/` - Create new opportunity
- `GET /opportunities/{id}` - Get opportunity by ID
- `PUT /opportunities/{id}` - Update opportunity
- `DELETE /opportunities/{id}` - Delete opportunity

### Advanced Query Endpoints (for Agent Testing)

#### Find Contacts from Accounts with High-Value Opportunities
```
GET /advanced/contacts/from-accounts/
```
**Parameters:**
- `min_value` (float): Minimum opportunity value (default: 10000)
- `min_likelihood` (float): Minimum likelihood percentage (default: 0.5)
- `skip` (int): Pagination offset (default: 0)
- `limit` (int): Records per page (default: 20)

**Example:** Find all contacts from accounts with opportunities ≥$10k and ≥50% likelihood
```
GET /advanced/contacts/from-accounts/?min_value=10000&min_likelihood=0.5
```

#### Find Opportunities by Region
```
GET /advanced/opportunities/by-region/
```
**Parameters:**
- `min_value` (float): Minimum opportunity value (default: 10000)
- `month` (int): Month 1-12 (default: 11)
- `year` (int): Year (default: 2024)
- `skip` (int): Pagination offset (default: 0)
- `limit` (int): Records per page (default: 20)

**Example:** Find opportunities ≥$10k for November 2024, grouped by region
```
GET /advanced/opportunities/by-region/?min_value=10000&month=11&year=2024
```

## Sample Data

The system is pre-populated with comprehensive test data using predefined arrays (no randomization):
- **1,000 Accounts** across various industries and regions
- **2,000 Leads** with different sources and statuses
- **5,000+ Contacts** (3-8 per account)
- **8,000+ Opportunities** (2-10 per account) with varying values and probabilities
- **Total: 16,000+ records** for comprehensive agent testing

## Pagination

All list endpoints support pagination:
- `skip`: Number of records to skip (default: 0)
- `limit`: Number of records per page (default: 20, max: 100)

**Response format:**
```json
{
  "items": [...],
  "total": 1000,
  "page": 1,
  "pages": 50,
  "per_page": 20
}
```

## Data Models

### Account
- Basic company information (name, industry, website, etc.)
- Location data (address, city, state, country, region)
- Business metrics (annual revenue, employee count)

### Lead
- Personal information (name, email, phone)
- Company details (company, job title, industry)
- Lead management (source, status, score, notes)

### Contact
- Personal information (name, email, phone)
- Professional details (job title, department)
- Account relationship (linked to account, primary contact flag)

### Opportunity
- Opportunity details (name, description, value, currency)
- Sales process (stage, probability, close date)
- Account relationship (linked to account)

## Database

- Uses SQLite by default (easily configurable for PostgreSQL)
- Automatic database initialization and data seeding
- Foreign key relationships between entities

## Testing Agent Queries

This API is designed to test agents with complex queries like:

1. **"Find all contacts from my CRM accounts for opportunities $10k and 50% likelihood"**
   ```
   GET /contacts/from-accounts/?min_value=10000&min_likelihood=0.5
   ```

2. **"Find all opportunities from my CRM accounts that are above $10k for November, grouped by region"**
   ```
   GET /opportunities/by-region/?min_value=10000&month=11&year=2024
   ```

The pagination system ensures agents must handle large datasets efficiently, making it a robust testing environment for CRM data queries.
