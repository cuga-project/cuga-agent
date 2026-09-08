from typing import Optional

from pydantic import BaseModel


class ApiDescription(BaseModel):
    app_name: str
    api_name: str
    api_description: Optional[str] = None
