from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class InputControlSpec(BaseModel):
    field_name: str
    label: str
    input_type: str = Field(description="'text', 'number', 'email', 'dropdown'")
    options: Optional[List[str]] = None
    placeholder: Optional[str] = ""
    is_required: bool = True


class DynamicAppSpec(BaseModel):
    app_title: str
    description: str
    required_inputs: List[InputControlSpec]
    prompt_template: str


class IntrospectRequest(BaseModel):
    url: str
    objective: str
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    cdp_url: Optional[str] = None
    start_fresh_browser: Optional[bool] = None


class ExecuteTaskRequest(BaseModel):
    url: str
    prompt: str
    extraction_schema: Optional[Dict[str, Any]] = None
    max_steps: Optional[int] = None
    run_with: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    cdp_url: Optional[str] = None
    start_fresh_browser: Optional[bool] = None


class TaskStatusResponse(BaseModel):
    task_id: str
    task_type: str
    status: str = Field(description="'queued', 'running', 'completed', 'failed'")
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_delivered: Optional[bool] = None
    webhook_delivery_attempts: int = 0
    webhook_delivered_at: Optional[str] = None
    cdp_url: Optional[str] = None
    queued_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
