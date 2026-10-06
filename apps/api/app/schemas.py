from pydantic import BaseModel, ConfigDict, EmailStr, Field

class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    type: str = "other"
    owner_name: str = Field(min_length=2)
    owner_email: EmailStr
    password: str = Field(min_length=12)

class Login(BaseModel):
    organization_id: str
    email: EmailStr
    password: str

class UserCreate(BaseModel):
    name: str = Field(min_length=2)
    email: EmailStr
    password: str = Field(min_length=12)
    role: str = "operator"

class ContactCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = ""
    phone: str | None = None
    email: EmailStr | None = None
    language: str | None = None
    timezone: str | None = None
    source: str = "manual"
    external_id: str | None = None

class ContactUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    language: str | None = None
    timezone: str | None = None
    status: str | None = None

class ConsentCreate(BaseModel):
    channel: str
    purpose: str = "engagement"
    status: str
    source: str = Field(min_length=2)

class ConversationCreate(BaseModel):
    contact_id: str
    channel: str

class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)

class WebhookMessage(BaseModel):
    organization_id: str
    external_event_id: str = Field(min_length=1, max_length=200)
    contact_phone: str
    content: str = Field(min_length=1, max_length=10000)

class DeliveryEvent(BaseModel):
    organization_id: str
    external_event_id: str = Field(min_length=1, max_length=200)
    message_id: str
    status: str
    external_id: str | None = None

class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=2000)

class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    source: str = Field(default="", max_length=300)
    content: str = Field(min_length=20, max_length=200000)
    knowledge_base_id: str | None = None

class KnowledgeDocumentUpdate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    source: str = Field(default="", max_length=300)
    content: str = Field(min_length=20, max_length=200000)

class KnowledgeQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=1500)

class KnowledgeAsk(KnowledgeQuestion):
    idempotency_key: str = Field(min_length=8, max_length=100)

class SuggestReply(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=100)

class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)
