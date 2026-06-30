from fastapi import FastAPI, HTTPException, Depends, Security, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import os
import time
import hmac
import hashlib
import base64
import sqlite3
import urllib.parse
from typing import List, Optional, Annotated, Dict
import logging
from openai import OpenAI
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Menu Intelligence API",
    description="Vision-based menu understanding service for structured extraction, translation, and dietary metadata.",
    version="1.0.3",
)

# CORS middleware for your iOS app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your app's domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()

load_dotenv()

# Environment variables (secure API key storage)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BACKEND_API_SECRET = os.getenv("BACKEND_API_SECRET", "your-secret-key")
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "16000"))

# Dynamic auth configuration
AUTH_WINDOW_SECONDS = int(os.getenv("AUTH_WINDOW_SECONDS", "300"))  # 5 minutes
MASTER_SECRET = os.getenv("MASTER_SECRET", "menu-scanner-hmac-key-2024")  # Should be changed in production

if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY in environment variables")

# Initialize OpenAI client
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Device storage management
class DeviceStorage:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or os.getenv("DATABASE_URL")
        self.use_postgres = self.database_url and self.database_url.startswith("postgres")
        
        if self.use_postgres:
            logger.info("🐘 Using PostgreSQL for device storage")
            self.db_path = None
        else:
            self.db_path = "devices.db"
            logger.info("🗃️ Using SQLite for device storage")
            
        self.init_db()
    
    def init_db(self):
        """Initialize the device storage database"""
        if self.use_postgres:
            self._init_postgres()
        else:
            self._init_sqlite()
    
    def _init_sqlite(self):
        """Initialize SQLite database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_secrets (
                    device_id TEXT PRIMARY KEY,
                    device_secret_b64 TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Migrate old column name if exists
            try:
                conn.execute("ALTER TABLE device_secrets RENAME COLUMN device_secret_hash TO device_secret_b64")
                conn.commit()
                logger.info("✅ Migrated database column: device_secret_hash -> device_secret_b64")
            except:
                # Column already renamed or doesn't exist
                pass
            conn.commit()
    
    def _init_postgres(self):
        """Initialize PostgreSQL database"""
        try:
            import psycopg2
            
            conn = psycopg2.connect(self.database_url)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS device_secrets (
                    device_id VARCHAR(255) PRIMARY KEY,
                    device_secret_b64 TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            cursor.close()
            conn.close()
            logger.info("✅ PostgreSQL database initialized")
            
        except ImportError:
            logger.error("❌ psycopg2 not installed. Install with: pip install psycopg2-binary")
            raise
        except Exception as e:
            logger.error(f"❌ Failed to initialize PostgreSQL: {e}")
            # Fallback to SQLite
            logger.info("🔄 Falling back to SQLite")
            self.use_postgres = False
            self.db_path = "devices.db"
            self._init_sqlite()
    
    def register_device(self, device_id: str, device_secret_b64: str) -> bool:
        """Register a new device or update existing device secret"""
        try:
            # Validate device secret
            device_secret = base64.b64decode(device_secret_b64)
            if len(device_secret) != 32:
                raise ValueError("Device secret must be 32 bytes")
            
            if self.use_postgres:
                return self._register_device_postgres(device_id, device_secret_b64)
            else:
                return self._register_device_sqlite(device_id, device_secret_b64)
                
        except Exception as e:
            logger.error(f"❌ Failed to register device {device_id}: {e}")
            return False
    
    def _register_device_sqlite(self, device_id: str, device_secret_b64: str) -> bool:
        """Register device in SQLite"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO device_secrets 
                (device_id, device_secret_b64, last_used_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (device_id, device_secret_b64))
            conn.commit()
        
        logger.info(f"✅ Device registered in SQLite: {device_id}")
        return True
    
    def _register_device_postgres(self, device_id: str, device_secret_b64: str) -> bool:
        """Register device in PostgreSQL"""
        import psycopg2
        
        conn = psycopg2.connect(self.database_url)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO device_secrets 
            (device_id, device_secret_b64, last_used_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (device_id) 
            DO UPDATE SET device_secret_b64 = EXCLUDED.device_secret_b64, 
                         last_used_at = CURRENT_TIMESTAMP
        """, (device_id, device_secret_b64))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"✅ Device registered in PostgreSQL: {device_id}")
        return True
    
    def get_device_secret(self, device_id: str) -> Optional[bytes]:
        """Get device secret for HMAC verification"""
        try:
            if self.use_postgres:
                return self._get_device_secret_postgres(device_id)
            else:
                return self._get_device_secret_sqlite(device_id)
        except Exception as e:
            logger.error(f"❌ Failed to get device secret for {device_id}: {e}")
            return None
    
    def _get_device_secret_sqlite(self, device_id: str) -> Optional[bytes]:
        """Get device secret from SQLite"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT device_secret_b64 FROM device_secrets WHERE device_id = ?",
                (device_id,)
            )
            result = cursor.fetchone()
            
            if result:
                # Update last used timestamp
                conn.execute(
                    "UPDATE device_secrets SET last_used_at = CURRENT_TIMESTAMP WHERE device_id = ?",
                    (device_id,)
                )
                conn.commit()
                
                return base64.b64decode(result[0])
            
            return None
    
    def _get_device_secret_postgres(self, device_id: str) -> Optional[bytes]:
        """Get device secret from PostgreSQL"""
        import psycopg2
        
        conn = psycopg2.connect(self.database_url)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT device_secret_b64 FROM device_secrets WHERE device_id = %s",
            (device_id,)
        )
        result = cursor.fetchone()
        
        if result:
            # Update last used timestamp
            cursor.execute(
                "UPDATE device_secrets SET last_used_at = CURRENT_TIMESTAMP WHERE device_id = %s",
                (device_id,)
            )
            conn.commit()
            
            cursor.close()
            conn.close()
            
            return base64.b64decode(result[0])
        
        cursor.close()
        conn.close()
        return None

# Initialize device storage
device_storage = DeviceStorage()

# Data models with detailed field descriptions
# Device registration models
class DeviceRegistrationRequest(BaseModel):
    device_id: str = Field(..., description="Unique device identifier")
    device_secret_b64: str = Field(..., description="Base64 encoded device secret")

class DeviceRegistrationResponse(BaseModel):
    status: str
    message: str
    device_id: str

class MenuAnalysisRequest(BaseModel):
    image: str = Field(..., description="Base64 encoded image data")
    target_language: str = Field(default="english", description="Target language for translation")
    user_id: Optional[str] = Field(None, description="Optional user identifier")
    app_version: Optional[str] = Field(None, description="App version for analytics")

class MenuItem(BaseModel):
    originalName: str = Field(
        ..., 
        description="Exact name from menu preserving original language and characters"
    )
    translatedName: str = Field(
        ..., 
        description="Translation of the dish name in the target language"
    )
    price: str = Field(
        ..., 
        description="Price as shown on menu with currency symbol (e.g., '$12.99', '¥50', '€15.50')"
    )
    description: str = Field(
        ..., 
        description="Original description from menu, or '[AI Generated] factual description' if not available"
    )
    translatedDescription: str = Field(
        ..., 
        description="Translation of description in target language; if AI-generated, prefix with the target-language equivalent of '[AI Generated] ' (e.g., Simplified Chinese: '[AI生成] ')"
    )
    estimatedIngredients: List[str] = Field(
        ..., 
        description="List of main ingredients translated to target language"
    )
    estimatedAllergens: List[str] = Field(
        ..., 
        description="Common allergens translated to target language (e.g., nuts, dairy, gluten, shellfish)"
    )
    cookingMethod: str = Field(
        ..., 
        description="Primary cooking method translated to target language (e.g., grilled, fried, steamed, baked, raw)"
    )
    dietaryLabels: List[str] = Field(
        ..., 
        description="Dietary tags translated to target language (e.g., vegetarian, vegan, gluten-free, spicy, halal)"
    )
    regionalCuisine: str = Field(
        ..., 
        description="Cuisine type translated to target language (e.g., Chinese, Italian, Mexican, Japanese)"
    )
    category: str = Field(
        ..., 
        description="Menu category translated to target language (e.g., appetizer, main, dessert, drink, side)"
    )
    isEstimated: bool = Field(
        ..., 
        description="True if any information (except name and price) was estimated by AI"
    )

class MenuResponse(BaseModel):
    items: List[MenuItem] = Field(
        ..., 
        description="Complete list of all menu items found in the image"
    )

class MenuAnalysisResponse(BaseModel):
    items: List[MenuItem]
    processing_time: float
    tokens_used: Optional[int] = None

class HealthResponse(BaseModel):
    status: str
    timestamp: float
    version: str

# Direct mapping from iOS TranslationLanguage rawValue to target language
def parse_ios_language(ios_language_raw: str) -> str:
    """Parse iOS TranslationLanguage rawValue and return appropriate target language"""
    
    # Create mapping from iOS rawValue to target language
    language_mapping = {
        # East Asian Languages
        "Chinese (简体中文)": "Simplified Chinese (简体中文)",
        "Traditional Chinese (繁體中文)": "Traditional Chinese (繁體中文)", 
        "Japanese (日本語)": "Japanese (日本語)",
        "Korean (한국어)": "Korean (한국어)",
        
        # Southeast Asian Languages
        "Vietnamese (Tiếng Việt)": "Vietnamese (Tiếng Việt)",
        "Thai (ภาษาไทย)": "Thai (ภาษาไทย)",
        "Indonesian (Bahasa Indonesia)": "Indonesian (Bahasa Indonesia)",
        "Malay (Bahasa Melayu)": "Malay (Bahasa Melayu)",
        
        # South Asian Languages
        "Hindi (हिन्दी)": "Hindi (हिन्दी)",
        "Bengali (বাংলা)": "Bengali (বাংলা)", 
        "Urdu (اردو)": "Urdu (اردو)",
        
        # European Languages
        "English": "English",
        "Spanish (Español)": "Spanish (Español)",
        "French (Français)": "French (Français)",
        "German (Deutsch)": "German (Deutsch)",
        "Italian (Italiano)": "Italian (Italiano)",
        "Portuguese (Português)": "Portuguese (Português)",
        "Russian (Русский)": "Russian (Русский)",
        "Dutch (Nederlands)": "Dutch (Nederlands)",
        
        # Middle Eastern Languages
        "Arabic (العربية)": "Arabic (العربية)",
        "Turkish (Türkçe)": "Turkish (Türkçe)"
    }
    
    # Try exact match first
    if ios_language_raw in language_mapping:
        return language_mapping[ios_language_raw]
    
    # Fallback: try to match by extracting language name
    lower_lang = ios_language_raw.lower()
    
    if "chinese" in lower_lang and "traditional" in lower_lang:
        return "Traditional Chinese (繁體中文)"
    elif "chinese" in lower_lang:
        return "Simplified Chinese (简体中文)"
    elif "japanese" in lower_lang:
        return "Japanese (日本語)"
    elif "korean" in lower_lang:
        return "Korean (한국어)"
    elif "vietnamese" in lower_lang:
        return "Vietnamese (Tiếng Việt)"
    elif "thai" in lower_lang:
        return "Thai (ภาษาไทย)"
    elif "indonesian" in lower_lang:
        return "Indonesian (Bahasa Indonesia)"
    elif "malay" in lower_lang:
        return "Malay (Bahasa Melayu)"
    elif "hindi" in lower_lang:
        return "Hindi (हिन्दी)"
    elif "bengali" in lower_lang:
        return "Bengali (বাংলা)"
    elif "urdu" in lower_lang:
        return "Urdu (اردو)"
    elif "spanish" in lower_lang:
        return "Spanish (Español)"
    elif "french" in lower_lang:
        return "French (Français)"
    elif "german" in lower_lang:
        return "German (Deutsch)"
    elif "italian" in lower_lang:
        return "Italian (Italiano)"
    elif "portuguese" in lower_lang:
        return "Portuguese (Português)"
    elif "russian" in lower_lang:
        return "Russian (Русский)"
    elif "dutch" in lower_lang:
        return "Dutch (Nederlands)"
    elif "arabic" in lower_lang:
        return "Arabic (العربية)"
    elif "turkish" in lower_lang:
        return "Turkish (Türkçe)"
    else:
        # Default to English
        return "English"

# Enhanced Authentication with HMAC signature verification
async def verify_dynamic_auth(
    x_device_id: Annotated[str, Header()],
    x_timestamp: Annotated[str, Header()],
    x_signature: Annotated[str, Header()],
    authorization: Annotated[str, Header()],
):
    """Verify HMAC-based dynamic authentication"""
    
    # Check if it's using old static token (temporary backward compatibility)
    if authorization.startswith("Bearer ") and authorization.replace("Bearer ", "") == BACKEND_API_SECRET:
        logger.warning("⚠️ Using deprecated static token authentication")
        return "legacy_auth"
    
    # For new dynamic auth, we expect "Bearer v1" or compatible versions
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header format")
    
    token = authorization.split(" ", 1)[1] if len(authorization.split(" ", 1)) > 1 else ""
    if token not in ("v1", "dynamic"):  # Support both v1 and legacy dynamic
        raise HTTPException(status_code=401, detail=f"Unsupported auth version: {token}")
    
    # Verify dynamic authentication
    try:
        # Parse timestamp
        timestamp = int(x_timestamp)
        current_time = int(time.time())
        
        # Check timestamp window (prevent replay attacks)
        if abs(current_time - timestamp) > AUTH_WINDOW_SECONDS:
            raise HTTPException(
                status_code=401, 
                detail=f"Request timestamp outside allowed window ({AUTH_WINDOW_SECONDS}s)"
            )
        
        # Get device-specific secret from storage
        device_secret = device_storage.get_device_secret(x_device_id)
        if device_secret is None:
            logger.warning(f"❌ Device not registered: {x_device_id}")
            raise HTTPException(
                status_code=401, 
                detail="Device not registered",
                headers={"X-Error-Code": "UNREGISTERED"}
            )
        
        # Generate expected signature using stored device secret
        message = f"{x_device_id}:{timestamp}"
        expected_signature = hmac.new(
            device_secret,
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        expected_signature_b64 = base64.b64encode(expected_signature).decode('utf-8')
        
        # Verify signature
        if not hmac.compare_digest(x_signature, expected_signature_b64):
            logger.warning(f"❌ Invalid signature for device {x_device_id}")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        logger.info(f"✅ Dynamic auth verified for device: {x_device_id}")
        return x_device_id
        
    except HTTPException as http_exc:
        # Re-raise HTTPException to preserve status code and headers (e.g., X-Error-Code: UNREGISTERED)
        raise http_exc
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp format")
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")

# Legacy authentication (for backward compatibility)
async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Legacy token verification - deprecated"""
    if credentials.credentials != BACKEND_API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    return credentials.credentials

# Health check endpoint
@app.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="healthy",
        timestamp=time.time(),
        version="1.0.0"
    )

# Device registration endpoint
@app.post("/auth/register", response_model=DeviceRegistrationResponse)
async def register_device(request: DeviceRegistrationRequest):
    """Register a new device with its secret key"""
    
    logger.info(f"🔐 Device registration request: {request.device_id}")
    
    # Validate device_id format (should be UUID)
    if not request.device_id or len(request.device_id) < 10:
        raise HTTPException(status_code=400, detail="Invalid device_id format")
    
    # Validate device_secret_b64
    try:
        device_secret = base64.b64decode(request.device_secret_b64)
        if len(device_secret) != 32:
            raise HTTPException(status_code=400, detail="Device secret must be 32 bytes")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid device_secret_b64 format")
    
    # Check if device already exists
    existing_secret = device_storage.get_device_secret(request.device_id)
    is_new_device = existing_secret is None
    
    # Register the device
    success = device_storage.register_device(request.device_id, request.device_secret_b64)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to register device")
    
    # Return appropriate status code
    from fastapi import Response
    if is_new_device:
        return DeviceRegistrationResponse(
            status="registered",
            message="Device registered successfully",
            device_id=request.device_id
        )
    else:
        return DeviceRegistrationResponse(
            status="updated", 
            message="Device secret updated successfully",
            device_id=request.device_id
        )

# Main analysis endpoint using OpenAI SDK with structured output
@app.post("/analyze-menu", response_model=MenuAnalysisResponse)
async def analyze_menu(
    request: MenuAnalysisRequest,
    device_id: str = Depends(verify_dynamic_auth)
):
    """Analyze menu image directly using GPT-4o Vision with structured output"""
    start_time = time.time()
    
    try:
        logger.info(f"Starting menu analysis for device: {device_id}, user: {request.user_id}")
        logger.info(f"Target language (raw): {request.target_language}")
        
        # Parse iOS language to target language
        target_lang = parse_ios_language(request.target_language)
        
        logger.info(f"Using target language: {target_lang}")
        logger.info(f"Max output tokens: {MAX_OUTPUT_TOKENS}")
        
        # Enhanced system prompt with strict target-language enforcement
        system_prompt = f"""
        You are a professional menu analyzer. Analyze the menu image and extract ALL visible items with complete, accurate details.
        
        INSTRUCTIONS:
        1) COMPLETENESS: Extract every dish, drink, dessert, side, and extra visible on the menu.
        2) ACCURACY: Keep original names exactly as shown, preserving all characters in originalName only.
        3) DESCRIPTIONS: If a description is missing, create a factual one and mark it as AI-generated.
        4) TRANSLATIONS: Provide all generated text fields in {target_lang} only.
        5) THOROUGHNESS: Fill out every field per schema.
        6) FORMATTING: Obey the schema exactly.
        
        LANGUAGE POLICY (STRICT):
        - Target language: {target_lang}.
        - Only originalName may remain in the source language; every other generated field MUST be in {target_lang}.
        - Do NOT mix languages or include transliterations/romanizations.
        - Use fluent, natural, and easy-to-understand wording in {target_lang}.
        
        FIELD REQUIREMENTS:
        - originalName: Exact text from menu (no modifications).
        - price: Include currency symbol as shown (e.g., '$12.99', '¥50', '€15.50').
        - description: Original menu text if available; otherwise create a factual description.
        - translatedDescription: Translate description into {target_lang}; if AI-generated, prefix with the localized equivalent of '[AI Generated] '.
        - estimatedIngredients: List of main ingredients in {target_lang}.
        - estimatedAllergens: Common allergens in {target_lang}.
        - cookingMethod: Primary method in {target_lang}.
        - dietaryLabels: Tags in {target_lang}.
        - regionalCuisine: Cuisine type in {target_lang}.
        - category: Menu section in {target_lang}.
        - isEstimated: true if any info beyond name/price was estimated.
        
        QUALITY:
        - Do not miss items.
        - Ensure cultural and linguistic appropriateness.
        - Keep wording fluent and easy to understand in {target_lang}.
        - Include all visible price information.
        """
        
        user_prompt = f"""
        Analyze this menu image and extract all menu items.
        
        Rules:
        - Preserve original names exactly as written
        - All generated text (except originalName) MUST be in {target_lang} only.
        - Do not include transliterations or mixed-language text.
        - Keep language fluent and easy to read.
        """
        
        # Use OpenAI Chat Completions API with structured output (supported parse helper)
        logger.info("Starting GPT-4o analysis with structured output (Chat Completions)")

        response = openai_client.responses.parse(
            model="gpt-4o",
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "input_text", "text": user_prompt},
                    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{request.image}"}
                ]}
            ],
            text_format=MenuResponse,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.1,
        )

        menu_data = MenuResponse.model_validate_json(response.output_text)

        processing_time = time.time() - start_time
        tokens_used = response.usage.total_tokens if response.usage else None
        
        logger.info(f"Analysis completed successfully in {processing_time:.2f}s")
        logger.info(f"Found {len(menu_data.items)} menu items")
        logger.info(f"Tokens used: {tokens_used}")
        
        # Log sample items for debugging
        if menu_data.items:
            logger.info(f"Sample item: {menu_data.items[0].originalName} -> {menu_data.items[0].translatedName}")
        
        return MenuAnalysisResponse(
            items=menu_data.items,
            processing_time=processing_time,
            tokens_used=tokens_used
        )
        
    except Exception as e:
        logger.error(f"Error during menu analysis: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Menu analysis failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
