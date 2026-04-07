from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import bigquery
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date

app = FastAPI()

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
    name: str = Field(..., min_length=1)
    address: str = Field(..., min_length=1)
    city: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1)
    postal_code: str = Field(..., min_length=1)
    property_type: str = Field(..., min_length=1)
    tenant_name: Optional[str] = None
    monthly_rent: float = Field(..., gt=0)
    bed_count: Optional[int] = None
    bath_count: Optional[float] = None
    square_footage: Optional[int] = None
    condition: Optional[str] = None
    notes: Optional[str] = None


class PropertyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    address: Optional[str] = Field(None, min_length=1)
    city: Optional[str] = Field(None, min_length=1)
    state: Optional[str] = Field(None, min_length=1)
    postal_code: Optional[str] = Field(None, min_length=1)
    property_type: Optional[str] = Field(None, min_length=1)
    tenant_name: Optional[str] = None
    monthly_rent: Optional[float] = Field(None, gt=0)
    bed_count: Optional[int] = None
    bath_count: Optional[float] = None
    square_footage: Optional[int] = None
    condition: Optional[str] = None
    notes: Optional[str] = None


class IncomeCreate(BaseModel):
    amount: float = Field(..., gt=0)
    date: date
    description: Optional[str] = None


class ExpenseCreate(BaseModel):
    amount: float = Field(..., gt=0)
    date: date
    category: str = Field(..., min_length=1)
    vendor: Optional[str] = None
    description: Optional[str] = None


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
# Helper: escape single quotes in strings for SQL
# ---------------------------------------------------------------------------
def sql_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return f"'{value.replace(chr(39), chr(39)+chr(39))}'"
    return str(value)


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
            monthly_rent,
            bed_count,
            bath_count,
            square_footage,
            condition,
            notes
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
def get_property(property_id: int, bq: bigquery.Client = Depends(get_bq_client)):
    """
    Returns a single property by its ID.
    """
    if property_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Property ID must be a positive integer"
        )
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
            monthly_rent,
            bed_count,
            bath_count,
            square_footage,
            condition,
            notes
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
            detail=f"Property {property_id} not found"
        )
    property = rows[0]
    return property


@app.post("/properties", status_code=status.HTTP_201_CREATED)
def create_property(prop: PropertyCreate, bq: bigquery.Client = Depends(get_bq_client)):
    """
    Creates a new property in the database.
    """
    new_id = get_next_id(bq, "properties", "property_id")
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.properties`
            (property_id, name, address, city, state, postal_code,
             property_type, tenant_name, monthly_rent,
             bed_count, bath_count, square_footage, condition, notes)
        VALUES
            ({new_id}, {sql_value(prop.name)}, {sql_value(prop.address)},
             {sql_value(prop.city)}, {sql_value(prop.state)},
             {sql_value(prop.postal_code)}, {sql_value(prop.property_type)},
             {sql_value(prop.tenant_name)}, {prop.monthly_rent},
             {sql_value(prop.bed_count)}, {sql_value(prop.bath_count)},
             {sql_value(prop.square_footage)}, {sql_value(prop.condition)},
             {sql_value(prop.notes)})
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
def update_property(property_id: int, prop: PropertyUpdate, bq: bigquery.Client = Depends(get_bq_client)):
    """
    Updates the details of an existing property.
    """
    if property_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Property ID must be a positive integer"
        )
    check_query = f"""
        SELECT
            property_id
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
            detail=f"Property {property_id} not found"
        )

    data = prop.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided to update"
        )

    updates = []
    for key, value in data.items():
        if isinstance(value, str):
            escaped = value.replace("'", "''")
            updates.append(f"{key} = '{escaped}'")
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
def delete_property(property_id: int, bq: bigquery.Client = Depends(get_bq_client)):
    """
    Deletes a property and all of its associated income and expense records.
    """
    if property_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Property ID must be a positive integer"
        )
    check_query = f"""
        SELECT
            property_id
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
            detail=f"Property {property_id} not found"
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
    message = {"message": f"Property {property_id} and its associated records have been deleted"}
    return message


# ---------------------------------------------------------------------------
# Income
# ---------------------------------------------------------------------------
@app.get("/income/{property_id}")
def get_income(property_id: int, bq: bigquery.Client = Depends(get_bq_client)):
    """
    Returns all income records for a specific property.
    """
    if property_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Property ID must be a positive integer"
        )
    check_query = f"""
        SELECT
            property_id
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
            detail=f"Property {property_id} not found"
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
def create_income(property_id: int, income: IncomeCreate, bq: bigquery.Client = Depends(get_bq_client)):
    """
    Creates a new income record for a specific property.
    """
    if property_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Property ID must be a positive integer"
        )
    check_query = f"""
        SELECT
            property_id
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
            detail=f"Property {property_id} not found"
        )

    new_id = get_next_id(bq, "income", "income_id")
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.income`
            (income_id, property_id, amount, date, description)
        VALUES
            ({new_id}, {property_id}, {income.amount}, '{income.date}',
             {sql_value(income.description)})
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
def get_expenses(property_id: int, bq: bigquery.Client = Depends(get_bq_client)):
    """
    Returns all expense records for a specific property.
    """
    if property_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Property ID must be a positive integer"
        )
    check_query = f"""
        SELECT
            property_id
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
            detail=f"Property {property_id} not found"
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
def create_expense(property_id: int, expense: ExpenseCreate, bq: bigquery.Client = Depends(get_bq_client)):
    """
    Creates a new expense record for a specific property.
    """
    if property_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Property ID must be a positive integer"
        )
    check_query = f"""
        SELECT
            property_id
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
            detail=f"Property {property_id} not found"
        )

    new_id = get_next_id(bq, "expenses", "expense_id")
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.expenses`
            (expense_id, property_id, amount, date, category, vendor, description)
        VALUES
            ({new_id}, {property_id}, {expense.amount}, '{expense.date}',
             {sql_value(expense.category)}, {sql_value(expense.vendor)},
             {sql_value(expense.description)})
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
def delete_expense(expense_id: int, bq: bigquery.Client = Depends(get_bq_client)):
    """
    Deletes a single expense record by its ID.
    """
    if expense_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expense ID must be a positive integer"
        )
    check_query = f"""
        SELECT
            expense_id
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
            detail=f"Expense {expense_id} not found"
        )

    try:
        bq.query(f"DELETE FROM `{PROJECT_ID}.{DATASET}.expenses` WHERE expense_id = {expense_id}").result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete expense record: {str(e)}"
        )
    message = {"message": f"Expense record {expense_id} has been deleted"}
    return message