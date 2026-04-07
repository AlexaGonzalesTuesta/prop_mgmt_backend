from fastapi import FastAPI, Depends, HTTPException, status, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from google.cloud import bigquery
from pydantic import BaseModel, Field, field_validator, ValidationError
from typing import Optional
from datetime import date
import re

app = FastAPI()

# ---------------------------------------------------------------------------
# Custom exception handlers for consistent error responses
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom handler for validation errors to provide consistent, user-friendly messages.
    """
    errors = []
    for error in exc.errors():
        field = error['loc'][-1] if error['loc'] else 'unknown'
        msg = error['msg']
        error_type = error['type']
        
        # Customize messages for common validation errors
        if error_type == 'int_parsing':
            errors.append(f"Field '{field}' must be a valid integer")
        elif error_type == 'float_parsing':
            errors.append(f"Field '{field}' must be a valid number")
        elif error_type == 'date_parsing':
            errors.append(f"Field '{field}' must be a valid date in YYYY-MM-DD format")
        elif error_type == 'missing':
            errors.append(f"Field '{field}' is required")
        elif 'greater_than' in error_type:
            errors.append(f"Field '{field}' must be greater than 0")
        elif 'string_too_short' in error_type:
            errors.append(f"Field '{field}' cannot be empty")
        else:
            errors.append(f"Field '{field}': {msg}")
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": errors}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ID = "mgmt-545-project-492014"
DATASET = "property_mgmt"

# ---------------------------------------------------------------------------
# Pydantic models for request bodies
# ---------------------------------------------------------------------------

class PropertyCreate(BaseModel):
    name: str = Field(..., min_length=1, description="Property name (cannot be empty)")
    address: str = Field(..., min_length=1, description="Street address")
    city: str = Field(..., min_length=1, description="City name")
    state: str = Field(..., min_length=1, description="State")
    postal_code: str = Field(..., min_length=1, description="Postal/ZIP code")
    property_type: str = Field(..., description="Type of property")
    tenant_name: Optional[str] = Field(None, description="Current tenant name (optional)")
    monthly_rent: float = Field(..., gt=0, description="Monthly rent amount (must be positive)")
    
    @field_validator('property_type')
    @classmethod
    def validate_property_type(cls, v):
        valid_types = ["Condo", "Single-Family Home", "Townhome", "Other"]
        if v not in valid_types:
            raise ValueError(f"Property type must be one of: {', '.join(valid_types)}")
        return v
    
    @field_validator('name', 'address', 'city', 'state', 'postal_code')
    @classmethod
    def validate_not_just_whitespace(cls, v):
        if v and not v.strip():
            raise ValueError("Cannot be only whitespace")
        return v.strip() if v else v
    
    @field_validator('tenant_name')
    @classmethod
    def validate_tenant_name(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Tenant name cannot be only whitespace")
        return v.strip() if v else v

class PropertyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, description="Property name")
    address: Optional[str] = Field(None, min_length=1, description="Street address")
    city: Optional[str] = Field(None, min_length=1, description="City name")
    state: Optional[str] = Field(None, min_length=1, description="State")
    postal_code: Optional[str] = Field(None, min_length=1, description="Postal/ZIP code")
    property_type: Optional[str] = Field(None, description="Type of property")
    tenant_name: Optional[str] = Field(None, description="Current tenant name")
    monthly_rent: Optional[float] = Field(None, gt=0, description="Monthly rent amount (must be positive)")
    
    @field_validator('property_type')
    @classmethod
    def validate_property_type(cls, v):
        if v is not None:
            valid_types = ["Condo", "Single-Family Home", "Townhome", "Other"]
            if v not in valid_types:
                raise ValueError(f"Property type must be one of: {', '.join(valid_types)}")
        return v
    
    @field_validator('name', 'address', 'city', 'state', 'postal_code', 'tenant_name')
    @classmethod
    def validate_not_just_whitespace(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Cannot be only whitespace")
        return v.strip() if v else v

class IncomeCreate(BaseModel):
    amount: float = Field(..., gt=0, description="Income amount (must be positive)")
    date: date = Field(..., description="Date in YYYY-MM-DD format")
    description: Optional[str] = Field(None, description="Optional description")
    
    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Description cannot be only whitespace")
        return v.strip() if v else v

class ExpenseCreate(BaseModel):
    amount: float = Field(..., gt=0, description="Expense amount (must be positive)")
    date: date = Field(..., description="Date in YYYY-MM-DD format")
    category: str = Field(..., description="Expense category")
    vendor: Optional[str] = Field(None, description="Vendor name (optional)")
    description: Optional[str] = Field(None, description="Optional description")
    
    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        valid_categories = ["Repair", "Insurance", "Taxes", "Cleaning and Maintenance", 
                           "Utilities", "Legal and Professional", "Other"]
        if v not in valid_categories:
            raise ValueError(f"Category must be one of: {', '.join(valid_categories)}")
        return v
    
    @field_validator('vendor', 'description')
    @classmethod
    def validate_not_just_whitespace(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Cannot be only whitespace")
        return v.strip() if v else v

# ---------------------------------------------------------------------------
# Dependency: BigQuery client
# ---------------------------------------------------------------------------

def get_bq_client():
    client = bigquery.Client()
    try:
        yield client
    finally:
        client.close()

# ---------------------------------------------------------------------------
# Helper: get next ID (BigQuery has no auto-increment)
# ---------------------------------------------------------------------------

def get_next_id(bq: bigquery.Client, table: str, id_column: str) -> int:
    query = f"""
        SELECT COALESCE(MAX({id_column}), 0) + 1 AS next_id
        FROM `{PROJECT_ID}.{DATASET}.{table}`
    """
    result = bq.query(query).result()
    for row in result:
        return row.next_id

# ---------------------------------------------------------------------------
# Helper: validate integer ID
# ---------------------------------------------------------------------------

def validate_id(id_value: int, id_name: str = "ID") -> int:
    """
    Validates that an ID is a positive integer.
    Raises HTTPException if invalid.
    """
    if id_value <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{id_name} must be a positive integer (received: {id_value})"
        )
    return id_value

# ---------------------------------------------------------------------------
# Helper: escape SQL strings
# ---------------------------------------------------------------------------

def escape_sql_string(value: Optional[str]) -> str:
    """
    Escapes single quotes in strings to prevent SQL injection.
    Returns 'NULL' for None values, otherwise returns escaped quoted string.
    """
    if value is None:
        return "NULL"
    # Escape single quotes by doubling them
    escaped = value.replace("'", "''")
    return f"'{escaped}'"

# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@app.get("/properties")
def get_properties(bq: bigquery.Client = Depends(get_bq_client)):
    """
    Returns all properties in the database.
    """
    query = f"""
        SELECT
            property_id,
            name,
            address,
            city,
            state,
            postal_code,
            property_type,
            tenant_name,
            monthly_rent
        FROM `{PROJECT_ID}.{DATASET}.properties`
        ORDER BY property_id
    """
    try:
        results = bq.query(query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    properties = [dict(row) for row in results]
    return properties

@app.get("/properties/{property_id}")
def get_property(
    property_id: int = Path(..., gt=0, description="ID of the property to retrieve (must be positive integer)"),
    bq: bigquery.Client = Depends(get_bq_client)
):
    """
    Returns a single property by its ID.
    """
    query = f"""
        SELECT
            property_id,
            name,
            address,
            city,
            state,
            postal_code,
            property_type,
            tenant_name,
            monthly_rent
        FROM `{PROJECT_ID}.{DATASET}.properties`
        WHERE property_id = {property_id}
    """
    try:
        results = bq.query(query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    rows = [dict(row) for row in results]
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property with ID {property_id} not found"
        )
    
    return rows[0]

@app.post("/properties", status_code=status.HTTP_201_CREATED)
def create_property(prop: PropertyCreate, bq: bigquery.Client = Depends(get_bq_client)):
    """
    Creates a new property in the database.
    """
    new_id = get_next_id(bq, "properties", "property_id")
    
    # Use helper function to escape SQL strings
    name = escape_sql_string(prop.name)
    address = escape_sql_string(prop.address)
    city = escape_sql_string(prop.city)
    state = escape_sql_string(prop.state)
    postal_code = escape_sql_string(prop.postal_code)
    property_type = escape_sql_string(prop.property_type)
    tenant = escape_sql_string(prop.tenant_name)
    
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.properties`
            (property_id, name, address, city, state, postal_code,
             property_type, tenant_name, monthly_rent)
        VALUES
            ({new_id}, {name}, {address}, {city},
             {state}, {postal_code}, {property_type},
             {tenant}, {prop.monthly_rent})
    """
    try:
        bq.query(query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create property: {str(e)}"
        )
    
    new_property = {"property_id": new_id, **prop.model_dump()}
    return new_property

@app.put("/properties/{property_id}")
def update_property(
    property_id: int = Path(..., gt=0, description="ID of the property to update (must be positive integer)"),
    prop: PropertyUpdate = ...,
    bq: bigquery.Client = Depends(get_bq_client)
):
    """
    Updates the details of an existing property.
    """
    # Check if property exists
    check_query = f"""
        SELECT property_id
        FROM `{PROJECT_ID}.{DATASET}.properties`
        WHERE property_id = {property_id}
    """
    try:
        results = bq.query(check_query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    rows = [dict(row) for row in results]
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property with ID {property_id} not found"
        )
    
    data = prop.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided to update. At least one field must be specified."
        )
    
    updates = []
    for key, value in data.items():
        if isinstance(value, str):
            # Escape single quotes to prevent SQL injection
            escaped_value = value.replace("'", "''")
            updates.append(f"{key} = '{escaped_value}'")
        elif value is None:
            updates.append(f"{key} = NULL")
        else:
            updates.append(f"{key} = {value}")
    
    set_clause = ", ".join(updates)
    update_query = f"""
        UPDATE `{PROJECT_ID}.{DATASET}.properties`
        SET {set_clause}
        WHERE property_id = {property_id}
    """
    try:
        bq.query(update_query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update property: {str(e)}"
        )
    
    updated_property = get_property(property_id, bq)
    return updated_property

@app.delete("/properties/{property_id}")
def delete_property(
    property_id: int = Path(..., gt=0, description="ID of the property to delete (must be positive integer)"),
    bq: bigquery.Client = Depends(get_bq_client)
):
    """
    Deletes a property and all of its associated income and expense records.
    """
    # Check if property exists
    check_query = f"""
        SELECT property_id
        FROM `{PROJECT_ID}.{DATASET}.properties`
        WHERE property_id = {property_id}
    """
    try:
        results = bq.query(check_query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    rows = [dict(row) for row in results]
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property with ID {property_id} not found"
        )
    
    try:
        bq.query(f"DELETE FROM `{PROJECT_ID}.{DATASET}.income` WHERE property_id = {property_id}").result()
        bq.query(f"DELETE FROM `{PROJECT_ID}.{DATASET}.expenses` WHERE property_id = {property_id}").result()
        bq.query(f"DELETE FROM `{PROJECT_ID}.{DATASET}.properties` WHERE property_id = {property_id}").result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete property: {str(e)}"
        )
    
    return {"message": f"Property {property_id} and all associated income and expense records have been deleted"}

# ---------------------------------------------------------------------------
# Income
# ---------------------------------------------------------------------------

@app.get("/income/{property_id}")
def get_income(
    property_id: int = Path(..., gt=0, description="ID of the property (must be positive integer)"),
    bq: bigquery.Client = Depends(get_bq_client)
):
    """
    Returns all income records for a specific property.
    """
    # Check if property exists
    check_query = f"""
        SELECT property_id
        FROM `{PROJECT_ID}.{DATASET}.properties`
        WHERE property_id = {property_id}
    """
    try:
        results = bq.query(check_query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    rows = [dict(row) for row in results]
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property with ID {property_id} not found"
        )
    
    query = f"""
        SELECT
            income_id,
            property_id,
            amount,
            date,
            description
        FROM `{PROJECT_ID}.{DATASET}.income`
        WHERE property_id = {property_id}
        ORDER BY date DESC
    """
    try:
        results = bq.query(query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    income_records = [dict(row) for row in results]
    return income_records

@app.post("/income/{property_id}", status_code=status.HTTP_201_CREATED)
def create_income(
    property_id: int = Path(..., gt=0, description="ID of the property (must be positive integer)"),
    income: IncomeCreate = ...,
    bq: bigquery.Client = Depends(get_bq_client)
):
    """
    Creates a new income record for a specific property.
    """
    # Check if property exists
    check_query = f"""
        SELECT property_id
        FROM `{PROJECT_ID}.{DATASET}.properties`
        WHERE property_id = {property_id}
    """
    try:
        results = bq.query(check_query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    rows = [dict(row) for row in results]
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property with ID {property_id} not found"
        )
    
    new_id = get_next_id(bq, "income", "income_id")
    desc = escape_sql_string(income.description)
    
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.income`
            (income_id, property_id, amount, date, description)
        VALUES
            ({new_id}, {property_id}, {income.amount}, '{income.date}', {desc})
    """
    try:
        bq.query(query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create income record: {str(e)}"
        )
    
    new_income = {"income_id": new_id, "property_id": property_id, **income.model_dump()}
    return new_income

# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------

@app.get("/expenses/{property_id}")
def get_expenses(
    property_id: int = Path(..., gt=0, description="ID of the property (must be positive integer)"),
    bq: bigquery.Client = Depends(get_bq_client)
):
    """
    Returns all expense records for a specific property.
    """
    # Check if property exists
    check_query = f"""
        SELECT property_id
        FROM `{PROJECT_ID}.{DATASET}.properties`
        WHERE property_id = {property_id}
    """
    try:
        results = bq.query(check_query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    rows = [dict(row) for row in results]
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property with ID {property_id} not found"
        )
    
    query = f"""
        SELECT
            expense_id,
            property_id,
            amount,
            date,
            category,
            vendor,
            description
        FROM `{PROJECT_ID}.{DATASET}.expenses`
        WHERE property_id = {property_id}
        ORDER BY date DESC
    """
    try:
        results = bq.query(query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    expense_records = [dict(row) for row in results]
    return expense_records

@app.post("/expenses/{property_id}", status_code=status.HTTP_201_CREATED)
def create_expense(
    property_id: int = Path(..., gt=0, description="ID of the property (must be positive integer)"),
    expense: ExpenseCreate = ...,
    bq: bigquery.Client = Depends(get_bq_client)
):
    """
    Creates a new expense record for a specific property.
    """
    # Check if property exists
    check_query = f"""
        SELECT property_id
        FROM `{PROJECT_ID}.{DATASET}.properties`
        WHERE property_id = {property_id}
    """
    try:
        results = bq.query(check_query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    rows = [dict(row) for row in results]
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property with ID {property_id} not found"
        )
    
    new_id = get_next_id(bq, "expenses", "expense_id")
    
    # Use helper function to escape SQL strings
    category = escape_sql_string(expense.category)
    vendor = escape_sql_string(expense.vendor)
    desc = escape_sql_string(expense.description)
    
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.expenses`
            (expense_id, property_id, amount, date, category, vendor, description)
        VALUES
            ({new_id}, {property_id}, {expense.amount}, '{expense.date}',
             {category}, {vendor}, {desc})
    """
    try:
        bq.query(query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create expense record: {str(e)}"
        )
    
    new_expense = {"expense_id": new_id, "property_id": property_id, **expense.model_dump()}
    return new_expense

@app.delete("/expenses/record/{expense_id}")
def delete_expense(
    expense_id: int = Path(..., gt=0, description="ID of the expense to delete (must be positive integer)"),
    bq: bigquery.Client = Depends(get_bq_client)
):
    """
    Deletes a single expense record by its ID.
    """
    # Check if expense exists
    check_query = f"""
        SELECT expense_id
        FROM `{PROJECT_ID}.{DATASET}.expenses`
        WHERE expense_id = {expense_id}
    """
    try:
        results = bq.query(check_query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )
    
    rows = [dict(row) for row in results]
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Expense with ID {expense_id} not found"
        )
    
    try:
        bq.query(f"DELETE FROM `{PROJECT_ID}.{DATASET}.expenses` WHERE expense_id = {expense_id}").result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete expense record: {str(e)}"
        )
    
    return {"message": f"Expense record {expense_id} has been deleted"}